using System.Text.Json.Nodes;
using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

/// <summary>Shared difficult/nightmare observation fit, including unknown-scale initial and multi-island recovery.</summary>
public sealed class SharedStructureFitPipeline : IDisposable
{
    private readonly ResourceCatalog _resources;
    private readonly Func<ResourceCatalogEntry, ResourceLoadResult<SharedStructureData>> _loadReference;
    private readonly Dictionary<string, PreparedReference> _references = new(StringComparer.Ordinal);
    private readonly Dictionary<string, StructureCatalog> _catalogs = new(StringComparer.Ordinal);
    private bool _disposed;
    private sealed record PreparedReference(StructureGeometry Geometry, FastPrepared? Fast, string CoordinatePackId);

    public SharedStructureFitPipeline(ResourceCatalog resources,
        Func<ResourceCatalogEntry, ResourceLoadResult<SharedStructureData>>? loadReference = null)
    { _resources = resources; _loadReference = loadReference ?? resources.LoadSharedStructure; }

    /// <summary>
    /// Fits one explicitly selected resource to this frame, from fresh scale estimation.
    /// Null bounds mean the complete client. This is the shared observational solver,
    /// not S0's entry/registered-floor/steady-session orchestration or identity selection.
    /// </summary>
    public JsonObject Fit(Mat frame, ResourceCatalogEntry target, Func<bool>? cancelled = null, Rect? observationBounds = null)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (frame.Empty() || frame.Type() != MatType.CV_8UC3) throw new ArgumentException("s1-structure-frame-invalid");
        var bounds = observationBounds ?? new Rect(0, 0, frame.Cols, frame.Rows);
        if (bounds.X < 0 || bounds.Y < 0 || bounds.Width <= 0 || bounds.Height <= 0 || bounds.Right > frame.Cols || bounds.Bottom > frame.Rows)
            throw new ArgumentException("Observation bounds must lie inside the frame.");
        using var scan = new Scan(this, null, frame, bounds, target);
        return scan.Fit(frame, target.MapId, target.Floor, cancelled ?? (() => false), bounds);
    }

    public StructureCandidateScanner CreateScanner(StructureCatalog catalog)
    {
        RequireCatalog(catalog);
        return new(catalog, (frame, map, floor, cancelled, bounds) =>
            {
                using var scan = PrepareScan(catalog, frame, bounds);
                return scan.Fit(frame, map, floor, cancelled, bounds);
            }, prepareFit: (frame, bounds) => PrepareScan(catalog, frame, bounds),
            prepareSharedFit: preparation => PrepareScan(catalog,preparation));
    }

    private IStructureScanFit PrepareScan(StructureCatalog catalog,StructureFramePreparation preparation)
    {
        ObjectDisposedException.ThrowIf(_disposed,this);RequireCatalog(catalog);
        return new Scan(this,catalog,preparation.Frame,preparation.Domain,shared:preparation);
    }

    public IStructureScanFit PrepareScan(StructureCatalog catalog, Mat frame, Rect bounds)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        RequireCatalog(catalog);
        return new Scan(this, catalog, frame, bounds);
    }

    private void RequireCatalog(StructureCatalog catalog)
    {
        if (!catalog.IsBoundTo(_resources)) throw new ArgumentException("s1-structure-catalog-resource-mismatch",nameof(catalog));
        // Preserve the original same-engine closure: a pipeline's reference cache cannot
        // be relabelled by a later catalog generation, root or candidate membership.
        lock (_catalogs)
        {
            if (_catalogs.TryGetValue(catalog.Mode,out var bound))
            {
                if (bound.ResourceKey != catalog.ResourceKey || bound.ModeRoot != catalog.ModeRoot || !bound.MapIds.SequenceEqual(catalog.MapIds))
                    throw new ArgumentException("s1-structure-catalog-resource-mismatch",nameof(catalog));
            }
            else _catalogs.Add(catalog.Mode,catalog);
        }
    }

    private PreparedReference? Reference(StructureCatalog? catalog, ResourceCatalogEntry? target, string mapId, string floor, out string reason)
    {
        reason = "shared-reference-unavailable";
        ResourceCatalogEntry? entry = target;
        if (entry is not null && !ReferenceEquals(entry, _resources.Find(entry.Mode, entry.Season, entry.Profile, entry.MapId, entry.Floor)))
        { reason = "catalog-entry-owner-mismatch"; return null; }
        if (entry is null && catalog is not null) entry = _resources.Find(catalog.Mode, catalog.Season, "shared-structure", mapId, floor);
        if (entry is null) return null;
        if (_references.TryGetValue(entry.StableKey, out var cached)) return cached;
        var loaded = _loadReference(entry);
        if (loaded.Value is null) { reason = loaded.Error?.Code ?? reason; return null; }
        var geometry = StructureGeometry.Open(loaded.Value);
        FastPrepared? fast = null;
        string? path = entry.StructureNpzPath;
        if (path is not null)
        {
            string fastPath = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(path))!, "fast-structure", Path.GetFileNameWithoutExtension(path) + "-fast.npz");
            if (File.Exists(fastPath))
            {
                try { fast = FastStructure.LoadPrepared(fastPath, loaded.Value); }
                catch (Exception error) when (error is IOException or InvalidDataException or ArgumentException or KeyNotFoundException) { fast = null; }
            }
        }
        string packId;
        try { packId = CoordinatePackId(entry); }
        catch (Exception error) when (error is IOException or InvalidDataException or JsonException or ArgumentException or KeyNotFoundException)
        { geometry.Dispose(); reason = "canonical-coordinate-resource-unavailable"; return null; }
        cached = new(geometry, fast, packId); _references.Add(entry.StableKey, cached); return cached;
    }

    private string CoordinatePackId(ResourceCatalogEntry entry)
    {
        string root = entry.Season == "s0" ? _resources.RootPath : Path.Combine(_resources.RootPath,"seasons",entry.Season,entry.Mode);
        string relative;
        if (entry.Season == "s0" && entry.Mode == "nightmare") relative = "references/nightmare-canonical-coordinate-pack.json";
        else
        {
            using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(root,entry.Season == "s0" ? "difficult-native-manifest.json" : "native-map-manifest.json")));
            relative = manifest.RootElement.GetProperty("runtimeInterfaces").GetProperty("coordinatePack").GetString()!;
        }
        string path = Path.GetFullPath(Path.Combine(root,relative));
        if (Path.IsPathRooted(relative) || !path.StartsWith(Path.GetFullPath(root)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("canonical-coordinate-resource-outside-context");
        using var pack = JsonDocument.Parse(File.ReadAllBytes(path));
        var adapter = new AuthorCoordinateAdapter(pack.RootElement);
        return adapter.PackId;
    }

    private sealed class Scan : IStructureScanFit
    {
        private readonly SharedStructureFitPipeline _owner;
        private readonly StructureCatalog? _catalog;
        private readonly ResourceCatalogEntry? _target;
        private readonly Mat _frame;
        private readonly Rect _domain;
        private readonly StructureFramePreparation? _shared;
        private bool _disposed, _primaryReady;
        private Observation? _primary;
        private IReadOnlyList<Island>? _islands;
        private readonly Dictionary<Rect, Observation?> _observations = [];
        public Scan(SharedStructureFitPipeline owner, StructureCatalog? catalog, Mat frame, Rect bounds, ResourceCatalogEntry? target = null,
            StructureFramePreparation? shared=null)
        { shared?.Require(frame,bounds);_owner = owner; _catalog = catalog; _frame = frame; _domain = bounds; _target = target; _shared=shared; }
        private Observation? Primary()
        {
            if(_shared is not null)return _shared.Primary();
            if (!_primaryReady) { _primary = Geometry.Current(_frame, _domain); _primaryReady = true; }
            return _primary;
        }
        private IReadOnlyList<Island> Islands() => _shared?.Islands()??(_islands ??= S1StructureObservations.Islands(_frame, _domain));
        private Observation? For(Island island)
        {
            if (Primary() is { } primary && primary.Bounds == island.Bounds) return primary;
            if (!_observations.TryGetValue(island.Bounds, out var context))
            { context = _shared is not null?_shared.For(island):Geometry.FromComponent(_frame, island.Bounds, (JsonObject)island.Component.DeepClone()); _observations.Add(island.Bounds, context); }
            return context;
        }

        public JsonObject Fit(Mat frame, string mapId, string floor, Func<bool> cancelled, Rect observationBounds)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (!ReferenceEquals(frame, _frame) || observationBounds != _domain) throw new ArgumentException("structure-scan-observation-context-mismatch");
            if (_target is null)
            {
                if (_catalog is null || !_catalog.MapIds.Contains(mapId) || !_catalog.Floors.Contains(floor)) throw new ArgumentException("structure-scan-catalog-context-mismatch");
            }
            else if (_target.MapId != mapId || _target.Floor != floor) throw new ArgumentException("structure-scan-catalog-context-mismatch");
            var reference = _owner.Reference(_catalog, _target, mapId, floor, out string unavailable);
            if (reference is null) return Failure(unavailable);
            var original = Primary();
            var initial = original is null ? Failure("manual-current-geometry-unavailable") : FitObservation(original, reference, cancelled);
            string reason = initial["reason"]?.ToString() ?? "";
            if (Success(initial) && cancelled()) return Failure("structure-work-obsolete");
            if (Success(initial) && (Flag(initial, "markerOccludedWallProposal") || Flag(initial, "markerOccludedWallObservation")))
            {
                var rejected = MarkerPeerRejection(reference.Geometry, initial, cancelled);
                if (rejected is not null) return rejected;
            }
            if (Success(initial) || reason.Contains("obsolete", StringComparison.Ordinal) || reason.Contains("ambiguous", StringComparison.Ordinal)
                || reason.Contains("budget-unresolved", StringComparison.Ordinal) || original is not null && Geometry.ObservedChains(_frame, original).Count > 0) return initial;
            var islands = Islands();
            if (islands.Count > 32) return new() { ["success"] = false, ["reason"] = "manual-component-budget-unresolved", ["componentCount"] = islands.Count, ["componentBudget"] = 32 };
            var accepted = new List<JsonObject>(); var diagnostics = new JsonArray(); var evidence = new List<Observation>(); bool unresolved = false;
            bool markerPeer = original is not null && MarkerProposals(_frame, original, []).Count > 0;
            foreach (var island in islands)
            {
                if (cancelled()) return Failure("structure-work-obsolete");
                if (original is not null && island.Bounds == original.Bounds)
                {
                    diagnostics.Add(new JsonObject { ["bounds"] = Bounds(island.Bounds), ["area"] = island.Component["area"]!.DeepClone(), ["success"] = false, ["reason"] = reason }); continue;
                }
                var context = For(island); if (context is null) continue;
                var measured = Geometry.ObservedChains(_frame, context);
                if (measured.Count > 0) evidence.Add(context); else if (MarkerProposals(_frame, context, measured).Count > 0) markerPeer = true;
                var fit = FitObservation(context, reference, cancelled); string fitReason = fit["reason"]?.ToString() ?? "";
                diagnostics.Add(new JsonObject { ["bounds"] = Bounds(island.Bounds), ["area"] = island.Component["area"]!.DeepClone(), ["success"] = Success(fit), ["reason"] = fitReason });
                if (fitReason.Contains("obsolete", StringComparison.Ordinal)) return Failure("structure-work-obsolete");
                if (fitReason.Contains("ambiguous", StringComparison.Ordinal) || fitReason.Contains("budget-unresolved", StringComparison.Ordinal)) unresolved = true;
                if (Success(fit) && !accepted.Any(old => PoseDistance(Matrix(fit["matrixSourceToClient"]!), Matrix(old["matrixSourceToClient"]!), reference.Geometry.Clean.Size()) <= 3)) accepted.Add(fit);
            }
            if (cancelled()) return Failure("structure-work-obsolete");
            if (unresolved || accepted.Count > 1) return new() { ["success"] = false, ["reason"] = "manual-component-pose-ambiguous", ["componentFits"] = diagnostics, ["acceptedPoseCount"] = accepted.Count, ["unresolved"] = unresolved };
            if (accepted.Count == 1)
            {
                var fit = accepted[0]; var matrix = Matrix(fit["matrixSourceToClient"]!);
                foreach (var context in evidence)
                {
                    if (cancelled()) return Failure("structure-work-obsolete");
                    if (JsonNode.DeepEquals(Bounds(context.Bounds), fit["observationBounds"])) continue;
                    bool consistent = ComponentConsistent(reference.Geometry, matrix, context);
                    if (cancelled()) return Failure("structure-work-obsolete");
                    if (!consistent) return new() { ["success"] = false, ["reason"] = "manual-components-inconsistent", ["componentFits"] = diagnostics, ["conflictingBounds"] = Bounds(context.Bounds) };
                }
                if (cancelled()) return Failure("structure-work-obsolete");
                if (markerPeer || Flag(fit, "markerOccludedWallProposal") || Flag(fit, "markerOccludedWallObservation"))
                { var rejected = MarkerPeerRejection(reference.Geometry, fit, cancelled); if (rejected is not null) return rejected; }
                fit["componentFits"] = diagnostics; fit["acceptedPoseCount"] = 1; fit["manualComponentFallback"] = true; return fit;
            }
            return new() { ["success"] = false, ["reason"] = diagnostics.Count > 0 ? "manual-components-current-geometry-rejected" : "manual-current-geometry-unavailable", ["componentFits"] = diagnostics };
        }

        private JsonObject FitObservation(Observation context, PreparedReference reference, Func<bool> cancelled)
        {
            if (cancelled()) return Failure("structure-work-obsolete");
            var fast = reference.Fast is null ? null : FastStructureFit.Evaluate(_frame, context, reference.Geometry, reference.Fast, cancelled: cancelled);
            JsonObject fit;
            string strategy;
            if (fast?.GeometryVerified == true && fast.Matrix is not null)
            {
                strategy = "shared-fast-alignment";
                fit = new() { ["success"] = true, ["reason"] = "experimental-fast-structure", ["matrixSourceToClient"] = Matrix(fast.Matrix),
                    ["inliers"] = Geometry.Nodes(context).Count, ["fastAlignmentDiagnostics"] = new JsonObject { ["pointGeometryReason"] = fast.Point?["reason"]?.DeepClone(), ["wallGeometryReason"] = fast.Wall?["reason"]?.DeepClone() } };
            }
            else
            {
                if (cancelled() || fast?.Reason.Contains("obsolete", StringComparison.Ordinal) == true) return Failure("structure-work-obsolete");
                strategy = "shared-initial-structure"; fit = S1StructureInitialFit.Evaluate(_frame, context, reference.Geometry, cancelled);
                if (fast is not null) fit["precedingFastReason"] = fast.Reason;
            }
            if (!Success(fit)) return fit;
            var matrix = Matrix(fit["matrixSourceToClient"]!);
            // The final consumer independently evaluates this exact shared reference and frame.
            var residual = FrameResidual.Evaluate(_frame, context, reference.Geometry.Validation, matrix);
            if (residual["verified"]?.GetValue<bool>() != true) return new() { ["success"] = false, ["reason"] = "canonical-current-frame-residual-unverified", ["residual"] = residual };
            var canonical = AlignmentObservationEvidence.CanonicalResidualEvidence(new Dictionary<string,object?> { ["verified"] = true },
                "game-rendered-shared-structure", reference.CoordinatePackId);
            if (!AlignmentObservationEvidence.RenderedProfileResidualIsVerified(canonical))
                return new() { ["success"] = false, ["reason"] = "canonical-current-frame-residual-unverified", ["residual"] = residual };
            foreach (var field in canonical) residual[field.Key] = JsonSerializer.SerializeToNode(field.Value);
            fit["selectedRouteCurrentFrameResidual"] = residual;
            bool markerObservation = Flag(fit, "markerOccludedWallProposal") ? (fit["diagnostics"]?["queryChains"]?.GetValue<int>() ?? 0) == 0
                : fit["diagnostics"]?["queryChains"] is null && Geometry.ObservedChains(_frame, context).Count == 0 && MarkerProposals(_frame, context, []).Count > 0;
            fit["markerOccludedWallObservation"] = markerObservation; fit["observationBounds"] = Bounds(context.Bounds);
            var component = (JsonObject)context.Component.DeepClone(); component["bounds"] = Bounds(context.Bounds); fit["observationComponent"] = component;
            fit["coordinateSpace"] = "shared-structure-to-client"; fit["routeProfileId"] = "shared-structure"; fit["strategy"] = strategy; return fit;
        }

        private bool ComponentConsistent(StructureGeometry reference, double[][] matrix, Observation context)
        {
            using var projected = Geometry.Warp(reference.Clean, matrix, _frame.Size());
            using var unknown = reference.Unknown is null ? null : Geometry.Warp(reference.Unknown, matrix, _frame.Size());
            if (RegionalGeometry.Evaluate(_frame, context, projected, unknown)["regionalEdgeVerified"]?.GetValue<bool>() != true) return false;
            return FrameResidual.Evaluate(_frame, context, reference.Validation, matrix)["verified"]?.GetValue<bool>() == true;
        }

        private JsonObject? MarkerPeerRejection(StructureGeometry reference, JsonObject fit, Func<bool> cancelled)
        {
            var islands = Islands(); if (islands.Count > 32) return new() { ["success"] = false, ["reason"] = "manual-component-budget-unresolved", ["componentCount"] = islands.Count, ["componentBudget"] = 32 };
            var matrix = Matrix(fit["matrixSourceToClient"]!);
            foreach (var island in islands)
            {
                if (cancelled()) return Failure("structure-work-obsolete");
                if (JsonNode.DeepEquals(Bounds(island.Bounds), fit["observationBounds"])) continue;
                var clipped = Rect.Intersect(island.Bounds, _domain); if (clipped.Width <= 0 || clipped.Height <= 0) continue;
                Observation? context; bool owned = clipped != island.Bounds;
                if (owned) context = Geometry.FromComponent(_frame, clipped, (JsonObject)island.Component.DeepClone()); else context = For(island);
                try
                {
                    if (context is null) continue;
                    var lines = Geometry.Lines(_frame, context);
                    if (reference.Unknown is not null)
                    {
                        using var unknown = Geometry.Warp(reference.Unknown, matrix, _frame.Size());
                        var observed = Geometry.Bytes(Geometry.Observed(_frame, context)); var excluded = Geometry.Bytes(unknown);
                        for (int i = 0; i < observed.Length; i++) if (excluded[i] > 0) observed[i] = 0;
                        using var mask = Geometry.FromBytes(_frame.Cols, _frame.Rows, observed);
                        lines = Geometry.VisibleWalls(context.Query, mask, Geometry.Nodes(context));
                    }
                    bool independent = true;
                    for (int axis = 0; axis < 2; axis++)
                    {
                        var planes = new List<double>();
                        foreach (var wall in lines.Where(l => l.Axis == axis)) if (!planes.Any(old => Math.Abs(wall.Normal-old) <= 3)) planes.Add(wall.Normal);
                        if (planes.Count < 2 || planes.Max()-planes.Min() < 24) { independent = false; break; }
                    }
                    if (!independent) continue;
                    bool consistent = ComponentConsistent(reference, matrix, context);
                    if (cancelled()) return Failure("structure-work-obsolete");
                    if (!consistent) return new() { ["success"] = false, ["reason"] = "manual-components-inconsistent", ["conflictingBounds"] = Bounds(context.Bounds) };
                }
                finally { if (owned) context?.Dispose(); }
            }
            return cancelled() ? Failure("structure-work-obsolete") : null;
        }
        public void Dispose()
        {
            if (_disposed) return; _disposed = true;
            if(_shared is null){_primary?.Dispose();foreach(var context in _observations.Values)context?.Dispose();}
            _observations.Clear();
        }
    }
    private static bool Flag(JsonObject value, string key) => value[key]?.GetValue<bool>() == true;
    private static JsonObject Failure(string reason) => new() { ["success"] = false, ["reason"] = reason };
    public void Dispose()
    {
        if (_disposed) return; _disposed = true; foreach (var reference in _references.Values) reference.Geometry.Dispose(); _references.Clear();
        lock (_catalogs) _catalogs.Clear();
    }
}
