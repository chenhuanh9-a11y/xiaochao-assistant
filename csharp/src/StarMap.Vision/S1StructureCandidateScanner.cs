using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Selection;

namespace StarMap.Vision;

public delegate JsonObject StructureFit(Mat frame, string mapId, string floor, Func<bool> cancelled, Rect observationBounds);

/// <summary>One scan's source-independent observation cache; disposed before the scan returns.</summary>
public interface IStructureScanFit : IDisposable
{
    JsonObject Fit(Mat frame, string mapId, string floor, Func<bool> cancelled, Rect observationBounds);
}

public sealed record StructureScanOptions(string Floor, string FloorSource = "operator-declared",
    Func<bool>? Cancelled = null, double? BudgetSeconds = null, Action<JsonObject>? OnProgress = null,
    IEnumerable<string>? CandidateOrder = null, string? PreferredMapId = null,
    Func<string, bool>? PreferredMaterialCommitted = null, IEnumerable<string>? PlannedMapIds = null,
    Func<string, bool>? FirstMaterialCommitted = null);

/// <summary>Scans the selected catalog, optionally stopping after qualified material commits. Failed fits remain UNKNOWN.</summary>
public sealed class StructureCandidateScanner
{
    public StructureCatalog Catalog { get; }
    private readonly StructureFit _fit;
    private readonly Func<double> _clock;
    private readonly Func<Mat, Rect, IStructureScanFit>? _prepareFit;
    private readonly Func<StructureFramePreparation, IStructureScanFit>? _prepareSharedFit;

    public StructureCandidateScanner(StructureCatalog catalog, StructureFit fit, Func<double>? clock = null,
        Func<Mat, Rect, IStructureScanFit>? prepareFit = null,
        Func<StructureFramePreparation, IStructureScanFit>? prepareSharedFit = null)
    {
        Catalog = catalog; _fit = fit; _clock = clock ?? (() => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency); _prepareFit = prepareFit;
        _prepareSharedFit=prepareSharedFit;
    }

    public JsonObject Scan(Mat input, StructureScanOptions options)
    {
        Validate(input,options);
        double started=_clock();
        // Public arbitrary Mat callers retain their original defensive copy.
        using var frame=input.Clone();
        return ScanFrame(frame,options,started,null);
    }

    /// <summary>Serially borrows this operation's owned frame/observations; never disposes the preparation owner.</summary>
    public JsonObject Scan(StructureFramePreparation preparation,StructureScanOptions options)
    {
        ArgumentNullException.ThrowIfNull(preparation);
        var frame=preparation.Frame;Validate(frame,options);
        return ScanFrame(frame,options,_clock(),preparation);
    }

    private void Validate(Mat input,StructureScanOptions options)
    {
        if (!Catalog.Floors.Contains(options.Floor) || options.FloorSource is not ("operator-declared" or "current-frame-verified"))
            throw new ArgumentException("s1-structure-floor-evidence-required");
        if (input.Empty() || input.Type() != MatType.CV_8UC3) throw new ArgumentException("s1-structure-frame-invalid");
        if (options.BudgetSeconds is { } budget && (!double.IsFinite(budget) || budget <= 0)) throw new ArgumentException("s1-structure-budget-invalid");
    }

    private JsonObject ScanFrame(Mat frame,StructureScanOptions options,double started,StructureFramePreparation? preparation)
    {
        var cancelled = options.Cancelled ?? (() => false);
        string frameKey = Convert.ToHexStringLower(SHA256.HashData(Geometry.Bytes(frame)));
        var bounds = new Rect(0, 0, frame.Cols, frame.Rows);
        using var prepared = preparation is not null && _prepareSharedFit is not null
            ? _prepareSharedFit(preparation):_prepareFit?.Invoke(frame, bounds);
        StructureFit fitOne = prepared is null ? _fit : prepared.Fit;
        var order = Catalog.MapIds.ToArray();
        if (options.CandidateOrder is not null)
        {
            var proposal = options.CandidateOrder.ToArray();
            if (proposal.Length == order.Length && proposal.All(v => v is not null) && proposal.Distinct().Count() == order.Length
                && proposal.ToHashSet().SetEquals(order)) order = proposal;
        }
        string? preferred = options.PreferredMapId is { } hint && order.Contains(hint) ? hint : null;
        if (preferred is not null) order = new[] { preferred }.Concat(order.Where(id => id != preferred)).ToArray();
        if (options.PlannedMapIds is not null)
        {
            var plan = options.PlannedMapIds.ToArray();
            if (plan.Length == 0 || plan.Distinct().Count() != plan.Length || plan.Except(order).Any()) throw new ArgumentException("s1-structure-plan-invalid");
            order = order.Where(plan.Contains).ToArray();
        }
        var supported = new JsonObject(); var checkedIds = new List<string>(); var outcomes = new JsonArray();
        int sequence = 0; double? firstVerified = null;
        JsonObject Report(string status)
        {
            sequence++;
            var poses = status == "cancelled" ? new JsonObject() : (JsonObject)supported.DeepClone();
            var ids = poses.Select(p => p.Key).ToArray();
            string reuse = status == "preferred-material-committed" ? "preferred-current-frame" : status == "first-material-committed" ? "first-current-frame" : "none";
            return new() { ["schemaVersion"] = 1, ["season"] = Catalog.Season, ["mode"] = Catalog.Mode, ["resourceKey"] = Catalog.ResourceKey,
                ["floor"] = options.Floor, ["floorSource"] = options.FloorSource, ["observedFloor"] = options.FloorSource == "current-frame-verified" ? options.Floor : "",
                ["frameKey"] = frameKey, ["status"] = status, ["scanComplete"] = status == "complete", ["updateSeq"] = sequence,
                ["expectedMapIds"] = StructureEvidence.Strings(Catalog.MapIds), ["checkedMapIds"] = StructureEvidence.Strings(checkedIds),
                ["unscannedMapIds"] = StructureEvidence.Strings(Catalog.MapIds.Except(checkedIds)), ["supportedMapIds"] = StructureEvidence.Strings(ids),
                ["unknownMapIds"] = StructureEvidence.Strings(Catalog.MapIds.Except(ids)), ["firstVerifiedPoseElapsedSeconds"] = firstVerified,
                ["excludedMapIds"] = new JsonArray(), ["poses"] = poses, ["outcomes"] = outcomes.DeepClone(), ["elapsedSeconds"] = _clock() - started,
                ["competitionComplete"] = false, ["uniqueIdentityVerified"] = false, ["identityHardGatePassed"] = false,
                ["automaticSelectionAuthorized"] = false, ["liveAuthorization"] = false, ["candidateReuse"] = reuse,
                ["scanSkipped"] = reuse != "none", ["reason"] = "supported-geometry-is-not-complete-identity-competition" };
        }
        string status = order.ToHashSet().SetEquals(Catalog.MapIds) ? "complete" : "subset-unresolved";
        foreach (string id in order)
        {
            if (cancelled()) { status = "cancelled"; break; }
            if (options.BudgetSeconds is { } limit && _clock() - started >= limit) { status = "budget-unresolved"; break; }
            JsonObject fit;
            try { fit = fitOne(frame, id, options.Floor, cancelled, bounds); }
            catch (Exception error) when (id == preferred && error is not OutOfMemoryException) { fit = new() { ["success"] = false, ["reason"] = "preferred-fit-unresolved" }; }
            if (fit is null)
            {
                if (id != preferred) throw new InvalidDataException("structure-fit-result-invalid");
                fit = new() { ["success"] = false, ["reason"] = "preferred-fit-unresolved" };
            }
            if (cancelled() || (fit["reason"]?.ToString() ?? "").Contains("obsolete", StringComparison.Ordinal)) { status = "cancelled"; break; }
            checkedIds.Add(id);
            bool ok = StructureEvidence.VerifiedPose(fit);
            outcomes.Add(new JsonObject { ["mapId"] = id, ["outcome"] = ok ? "supported" : "unknown", ["reason"] = fit["reason"]?.ToString() ?? "" });
            if (!ok) continue;
            if (options.OnProgress is not null && firstVerified is null) firstVerified = _clock() - started;
            supported[id] = fit.DeepClone();
            options.OnProgress?.Invoke(Report("partial"));
            bool Committed(Func<string, bool>? callback)
            {
                try { return callback?.Invoke(id) == true; }
                catch (Exception error) when (error is not OutOfMemoryException) { return false; }
            }
            if (Committed(options.FirstMaterialCommitted)) { status = "first-material-committed"; break; }
            if (id == preferred && Committed(options.PreferredMaterialCommitted)) { status = "preferred-material-committed"; break; }
        }
        if (cancelled()) status = "cancelled";
        return Report(status);
    }

    public static JsonArray RankRows(JsonObject report, StructureCatalog catalog)
    {
        var rows = new List<JsonObject>();
        foreach (var node in report["supportedMapIds"]!.AsArray())
        {
            string id = node!.GetValue<string>(); var fit = report["poses"]![id]!;
            double score = 0;
            var scoreNode = fit["selectedRouteCurrentFrameResidual"]?["withinThreePixels"];
            if (scoreNode is JsonValue boolean && boolean.TryGetValue<bool>(out bool flag)) score = flag ? 1 : 0;
            else if (scoreNode?.GetValueKind() == System.Text.Json.JsonValueKind.Number && double.TryParse(scoreNode.ToString(),
                System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double number) && double.IsFinite(number)) score = number;
            rows.Add(new() { ["mapId"] = id, ["name"] = catalog.MapNames[id], ["floor"] = report["floor"]!.DeepClone(), ["score"] = score * 100,
                ["strongSuggestion"] = false, ["identityHardGatePassed"] = false, ["identityEvidence"] = "s1-current-structure-proposal",
                ["reason"] = "结构匹配，地图身份待确认", ["structurePose"] = fit.DeepClone(), ["structureScan"] = new JsonObject {
                    ["scanComplete"] = report["scanComplete"]!.DeepClone(), ["competitionComplete"] = false,
                    ["unknownMapIds"] = report["unknownMapIds"]!.DeepClone(), ["unscannedMapIds"] = report["unscannedMapIds"]!.DeepClone() } });
        }
        return new JsonArray(rows.OrderByDescending(row => row["score"]!.GetValue<double>()).ThenBy(row => row["mapId"]!.GetValue<string>(), StringComparer.Ordinal).Select(row => (JsonNode)row).ToArray());
    }
}
