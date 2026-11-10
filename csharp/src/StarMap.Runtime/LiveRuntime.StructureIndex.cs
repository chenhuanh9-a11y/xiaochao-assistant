using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Selection;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private StructureIndexFilter? _structureIndex;
    private BoundResources? _structureIndexResources;
    private SharedStructureFitPipeline? _structureFits;
    private BoundResources? _structureFitResources;
    private bool _structureIndexEntered;
    private string? _s1ProvisionalMapId;
    private CaptureBindingScope? _s1PreferenceBinding;
    private ClientFrameContext? _s1PreferenceClient;
    private bool IsStructureProvisional => _selection.Season == "s1" && _identitySource == "s1-structure-provisional";
    private string? EffectiveMapId => _mapId ?? _s1ProvisionalMapId;

    private void DisposeStructureIndex()
    {
        _structureIndex?.Dispose(); _structureIndex = null; _structureIndexResources = null;
        _structureFits?.Dispose(); _structureFits = null; _structureFitResources = null;
    }

    // The single runtime worker owns these immutable resource preparations.
    // No frame, scale estimate or alignment transform is cached here.
    private SharedStructureFitPipeline GetStructureFits(BoundResources bound)
    {
        if (!ReferenceEquals(_structureFitResources, bound) || _structureFits is null)
        {
            _structureFits?.Dispose(); _structureFits = new(bound.Catalog, bound.LoadReference); _structureFitResources = bound;
        }
        return _structureFits;
    }

    private StructureIndexFilter GetStructureIndex(BoundResources bound, long epoch, CancellationToken token, CapturedClientFrame captured)
    {
        if (ReferenceEquals(_structureIndexResources, bound) && _structureIndex is not null) return _structureIndex;
        var created = new StructureIndexFilter(bound.Catalog, bound.StructureCatalog!, _options.StructureIndexAssetDirectory);
        try
        {
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                DisposeStructureIndex(); _structureIndex = created; _structureIndexResources = bound; return created;
            }
        }
        catch { created.Dispose(); throw; }
    }

    private RuntimeOperationResult ScanStructure(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string floor, Mat frame, JsonElement preparation)
    {
        StructureEvidenceSession session; ObservationTicket ticket; StructureCandidatePool pool; string floorSource;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            session = _structureSession ??= new(bound.StructureCatalog!, _roundId!);
            pool = _structurePool ?? throw new InvalidOperationException("structure-round-pool-unavailable");
            ticket = session.BeginObservation(floor); _structureTicket = ticket;
            floorSource = _manualFloor is null ? "current-frame-verified" : "operator-declared";
        }
        JsonObject? indexReport = null;
        string poolResult = "structure-coordinate-unknown";
        JsonObject gate = new() { ["status"] = "unavailable", ["reason"] = "structure-index-resources-unavailable" };
        try
        {
            var index = GetStructureIndex(bound, epoch, token, captured);
            var anchor = index.DetectAnchor(frame, () => Obsolete(epoch, token, captured, bound)); gate = anchor.ToJson();
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                if (anchor.Status == "located") _structureIndexEntered = true;
                _recognitionPath = _structureIndexEntered ? "structure-index" : "ordinary";
            }
            // An explicit floor override can guide fitting. Contradictory raw
            // floor evidence cannot eliminate maps from the structural ledger.
            if (_structureIndexEntered && _observedFloor == floor)
                indexReport = index.Evaluate(frame, pool.Remaining, floor, anchor, () => Obsolete(epoch, token, captured, bound));
        }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or JsonException or ArgumentException)
        { indexReport = new() { ["coordinateVerified"] = false, ["reason"] = "structure-index-resources-unavailable", ["detail"] = error.Message }; }
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (!ReferenceEquals(_structurePool, pool)) return Receipt(request, false, "structure-observation-obsolete");
            if (indexReport is not null) poolResult = ApplyStructureIndex(pool, bound, floor, indexReport);
            if (_s1ProvisionalMapId is { } provisional && !pool.Remaining.Contains(provisional))
            { _s1ProvisionalMapId = null; _pose = null; _identitySource = "s1-structure-candidates"; RevokeDisplay("provisional-map-excluded"); }
        }
        var pipeline = GetStructureFits(bound);
        var scanner = pipeline.CreateScanner(bound.StructureCatalog!);
        JsonObject? latest = null;
        bool materialCommitted = false;
        string? preferred = _s1ProvisionalMapId is { } prior && pool.Remaining.Contains(prior)
            && _s1PreferenceBinding is not null && _s1PreferenceBinding == _capture.BindingScope
            && _s1PreferenceClient == captured.Context ? prior : null;
        var report = scanner.Scan(frame, new(floor, floorSource, CandidateOrder: session.PriorityOrder(floor),
            PlannedMapIds: pool.Remaining, PreferredMapId: preferred,
            Cancelled: () => Obsolete(epoch, token, captured, bound), OnProgress: progress =>
            {
                lock (_sync)
                {
                    if (Obsolete(epoch, token, captured, bound) || _structureTicket != ticket) return;
                    if (session.Commit(progress, ticket)) { latest = progress; PublishStructure(progress); }
                }
            }, FirstMaterialCommitted: TryCurrentMaterial));
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (_structureTicket != ticket || !session.Commit(report, ticket))
                return FailCurrent(request, epoch, "structure-observation-obsolete");
            PublishStructure(report);
            return Receipt(request, materialCommitted || _candidates.Length > 0, _reason);
        }

        bool TryCurrentMaterial(string id)
        {
            if (latest?["poses"]?[id] is not JsonObject fit || !StructureEvidence.VerifiedPose(fit)) return false;
            var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season, "shared-structure", id, floor);
            var reference = entry is null ? null : bound.LoadReference(entry).Value;
            if (entry is null || reference is null) return false;
            var matrix = fit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
            var display = PrepareDisplay(bound, entry, matrix, reference, _selection.ProfileId, token);
            if (display.Material.Layer is null) return false;
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                if (_structureTicket != ticket || !ReferenceEquals(_structurePool, pool) || !pool.Remaining.Contains(id)) return false;
                if (_s1ProvisionalMapId != id) { RevokeDisplay("provisional-map-changed"); _alignment = null; }
                _s1ProvisionalMapId = id; _identitySource = "s1-structure-provisional";
                _s1PreferenceBinding = _capture.BindingScope; _s1PreferenceClient = captured.Context;
            }
            var prepared = new PreparedRuntimeFit(entry, fit, matrix, display, null, null,
                new() { ["attempted"] = false, ["reason"] = "explicit-domain-independent-fit" });
            Fit(request, epoch, token, captured, bound, id, floor, frame, prepared);
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                materialCommitted = _structureTicket == ticket && ReferenceEquals(_structurePool, pool) && _s1ProvisionalMapId == id && RenderCurrent();
                if (materialCommitted && pool.AcceptCurrentMaterial(id))
                { _mapId = id; _s1ProvisionalMapId = null; _identitySource = "structure-index-locked"; }
                Publish(); return materialCommitted;
            }
        }

        void PublishStructure(JsonObject result)
        {
            // Ordinary fitting proposes current pixels; only verified index
            // contradictions change the round pool. Unknown fits cannot vote.
            _candidates = result["supportedMapIds"]!.AsArray().Select(id => id!.GetValue<string>()).Where(pool.Remaining.Contains)
                .Select(id => new RuntimeCandidate(id, "shared-structure", 0, false, true)).ToArray();
            _selected = Array.FindIndex(_candidates, row => row.MapId == EffectiveMapId);
            if (_selected < 0 && _candidates.Length > 0) _selected = 0;
            bool current = RenderCurrent();
            if (!current && !IsStructureProvisional) _identitySource = "s1-structure-candidates";
            _reason = poolResult == "structure-coordinate-conflict-retained" ? poolResult
                : current ? pool.LockedMapId is not null ? "s1-structure-index-locked" : "s1-candidate-material-current"
                : "s1-structure-candidates-unresolved";
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "s1-structure", floor = preparation.GetProperty("floor"), report = result,
                index = indexReport, gate, poolResult, session = session.Snapshot(), preparation }, Json);
            Publish();
        }
    }

    private static string ApplyStructureIndex(StructureCandidatePool pool, BoundResources bound, string floor, JsonObject report)
    {
        if (report["mode"]?.ToString() != bound.StructureCatalog!.Mode || report["resourceKey"]?.ToString() != pool.ResourceId
            || report["floor"]?.ToString() != floor || report["evaluatedMapIds"] is not JsonArray evaluated
            || report["contradictedMapIds"] is not JsonArray bad || report["candidateChecks"] is not JsonArray checks)
            return "structure-coordinate-unknown";
        try
        {
            return pool.Observe(report["coordinateVerified"]?.GetValue<bool>() == true,
                evaluated.Select(id => id!.GetValue<string>()).ToArray(), bad.Select(id => id!.GetValue<string>()).ToArray(),
                checks.Select(row => new StructureIndexCheck(row!["mapId"]!.GetValue<string>(), row["explicitContradiction"]!.GetValue<bool>())).ToArray());
        }
        catch (Exception error) when (error is InvalidOperationException or NullReferenceException or FormatException)
        { return "structure-coordinate-unknown"; }
    }
}
