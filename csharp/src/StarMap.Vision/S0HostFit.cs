using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

/// <summary>A native-reference hint acknowledged by this exact owner/epoch/map/floor/resource.</summary>
public sealed record S0HostPosePrior(object OwnerScope, long Epoch, ResourceCatalogEntry Target, double[][] Matrix);

/// <summary>Caller-owned confirmed group state. A relation is not a verified target-floor pose.</summary>
public sealed record S0HostGroupState(object OwnerScope, long Epoch, string Mode, string Season, string MapId,
    string RouteProfileId, IReadOnlySet<string> VerifiedFloors, IReadOnlySet<string> RelationFloors,
    S0HostPosePrior? CommittedTargetPose = null, EstablishedGroupContext? Geometry = null);

public sealed record S0HostFitOptions
{
    public object? OwnerScope { get; init; }
    public long Epoch { get; init; }
    public S0HostPosePrior? PosePrior { get; init; }
    public S0HostGroupState? Group { get; init; }
    public bool SteadyRequested { get; init; }
    public bool SteadyEnabled { get; init; } = true;
    public bool FastAlignmentEnabled { get; init; } = true;
    public bool PoseIndexCandidatesEnabled { get; init; }
}

/// <summary>One immutable call context for an explicitly installed stateful producer.</summary>
public sealed record S0HostStageContext(Mat Frame, ResourceCatalogEntry Target, SharedStructureData Reference,
    string CanonicalPackId, JsonObject? EntryCandidate, S0HostFitOptions Options, Func<bool> Cancelled);

/// <summary>
/// Optional original steady/group/index producers. None means unavailable, not
/// permission to substitute fresh initial fitting for a requested stateful path.
/// The runtime owns commits, material acknowledgement and owner/epoch validity.
/// </summary>
public sealed record S0HostFitServices(
    Func<S0HostStageContext, JsonObject?>? Steady = null,
    Func<S0HostStageContext, JsonObject?>? EstablishedGroup = null,
    Func<S0HostStageContext, bool, JsonObject?>? PoseIndexInitial = null);

/// <summary>Fit proposal only. Reference/pack are the actual resources consumed, never display authority.</summary>
public sealed record S0HostFitResult(JsonObject Fit, SharedStructureData? Reference, string? CanonicalPackId,
    JsonArray Stages, JsonArray Events);

/// <summary>
/// Original implicit-domain S0 _AlignmentHost.fit caller. Reuses the common
/// numerical kernels; unlike an explicit structural scan it preserves the
/// default observation ROI, entry candidates and original fallback/rejection
/// order. Instances own prepared masks and are not concurrent session writers.
/// </summary>
public sealed partial class S0HostFit : IDisposable
{
    private readonly ResourceCatalog resources;
    private readonly EntryPassportCatalog catalog;
    private readonly SideEntryRanker side;
    private readonly MainEntryRanker main;
    private readonly S0HostFitServices services;
    private readonly Func<ResourceCatalogEntry, ResourceLoadResult<SharedStructureData>> loadReference;
    private readonly Dictionary<string, Prepared> prepared = new(StringComparer.Ordinal);
    private static readonly System.Text.Json.JsonSerializerOptions DiagnosticJson = new()
    { NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowNamedFloatingPointLiterals };
    private bool disposed;

    public S0HostFit(ResourceCatalog resources, EntryPassportCatalog catalog, S0HostFitServices? services = null,
        Func<ResourceCatalogEntry, ResourceLoadResult<SharedStructureData>>? loadReference = null)
    {
        if (catalog.Season != "s0" || !string.Equals(Path.TrimEndingDirectorySeparator(resources.RootPath),
            Path.TrimEndingDirectorySeparator(catalog.ResourceRoot), OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            throw new ArgumentException("S0 Host and entry catalog must use the same S0 resource context.");
        this.resources = resources; this.catalog = catalog; this.services = services ?? new();
        this.loadReference = loadReference ?? resources.LoadSharedStructure;
        side = new(catalog, resources); main = new(catalog, resources);
    }

    public S0HostFitResult Fit(Mat frame, ResourceCatalogEntry target, JsonObject? entryCandidate = null,
        S0HostFitOptions? options = null, Func<bool>? cancelled = null, CancellationToken cancellation = default)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        if (frame.Empty() || frame.Type() != MatType.CV_8UC3) throw new ArgumentException("Expected the complete BGR8 client frame.", nameof(frame));
        options ??= new();
        var trace = new JsonArray(); var events = new JsonArray();
        bool Obsolete() => cancellation.IsCancellationRequested || cancelled?.Invoke() == true;
        void Mark(string stage, string? outcome = null) => trace.Add(new JsonObject { ["stage"] = stage, ["outcome"] = outcome });
        void Log(string name, JsonObject? values = null)
        {
            var item = values?.DeepClone().AsObject() ?? new(); item["event"] = name; item["mapId"] = target.MapId; item["floor"] = target.Floor;
            events.Add(item); if (events.Count > 24) events.RemoveAt(0);
        }
        Prepared? reference = null;
        S0HostFitResult Done(JsonObject fit) => new(fit, reference?.Geometry.Source, reference?.CanonicalPackId, trace, events);
        if (Obsolete()) return Done(Failure("structure-work-obsolete"));
        if (!TargetCurrent(target))
            return Done(Failure("s0-host-target-resource-context-mismatch"));
        string identityProfile = catalog.MapProfiles[target.MapId];
        reference = Prepare(target, out string unavailable);
        if (reference is null) return Done(Failure(unavailable));
        var candidate = entryCandidate?.DeepClone().AsObject();
        var group = options.Group;
        if (group is not null && (!ReferenceEquals(group.OwnerScope, options.OwnerScope) || options.OwnerScope is null ||
            group.Epoch != options.Epoch || group.Mode != target.Mode || group.Season != target.Season || group.MapId != target.MapId))
            return Done(Failure("s0-host-group-context-mismatch"));
        var stageContext = new S0HostStageContext(frame, target, reference.Geometry.Source, reference.CanonicalPackId, candidate, options, Obsolete);
        try
        {
            // Original steady executes before context extraction and any fast
            // proposal. A non-null failure is terminal, just like success.
            if (options.SteadyRequested && options.SteadyEnabled)
            {
                Mark("steady");
                if (options.OwnerScope is null || services.Steady is null) return Done(Failure("s0-steady-fit-unavailable"));
                var steady = services.Steady(stageContext);
                if (Obsolete()) return Done(Failure("structure-work-obsolete"));
                if (steady is not null) return Done(steady.DeepClone().AsObject());
            }
            if (group?.VerifiedFloors.Count > 0 && services.EstablishedGroup is null && group.Geometry is null)
            {
                Mark("established-group", "implementation-unavailable");
                return Done(Failure("s0-established-group-fit-unavailable"));
            }
            using var current = Geometry.Current(frame);
            JsonObject? fitted = null; string strategy = "shared-fast-alignment";
            if (options.FastAlignmentEnabled && Environment.GetEnvironmentVariable("IDENTITYV_FAST_ALIGNMENT") != "0")
            {
                bool attemptedLocal = false;
                if (options.PosePrior is { } hint)
                {
                    var prior = ScopedPrior(hint, options, target, reference);
                    if (prior is not null)
                    {
                        attemptedLocal = true; Mark("fast-prior");
                        fitted = Fast(frame, current, reference, prior, true, Obsolete, Log);
                        Log("experimental-pose-prior", new() { ["accepted"] = IsSuccess(fitted), ["fallback"] = fitted is null, ["priorMatrix"] = Matrix(prior.Matrix) });
                    }
                    else Log("experimental-pose-prior", new() { ["accepted"] = false, ["fallback"] = true, ["reason"] = "pose-prior-context-or-matrix-mismatch" });
                }
                if (fitted is null)
                {
                    // A failed external local hint must not reintroduce an old
                    // committed scale in the unrestricted fallback.
                    var prior = !attemptedLocal && group?.VerifiedFloors.Contains(target.Floor) == true && group.CommittedTargetPose is { } committed
                        ? ScopedPrior(committed, options, target, reference) : null;
                    Mark("fast-global", prior is null ? "fresh-scale" : "committed-target-floor-scale");
                    fitted = Fast(frame, current, reference, prior, false, Obsolete, Log);
                }
            }
            bool entryFirst = identityProfile == "shared-structure";
            JsonObject? RecoverGroup(string stage, bool unconditional)
            {
                if (group is null || group.VerifiedFloors.Count == 0 || !unconditional && !group.RelationFloors.Contains(target.Floor)) return null;
                Mark(stage);
                var recovery = services.EstablishedGroup is { } supplied ? supplied(stageContext)
                    : EvaluateEstablishedGroup(stageContext).Fit;
                if (Obsolete()) return Failure("structure-work-obsolete");
                if (recovery is null) return Failure("s0-established-group-fit-unavailable");
                // The verified same-floor branch is terminal even on a miss.
                if (unconditional || IsSuccess(recovery) || Reason(recovery) != "structure-current-geometry-failed" || Flag(recovery, "sharedProjectionGeometryInsufficient")) return recovery;
                return null;
            }
            if (fitted is null && entryFirst && candidate is null && group?.VerifiedFloors.Contains(target.Floor) == true && group.RouteProfileId == "shared-structure")
            {
                fitted = RecoverGroup("established-verified-target-floor", true); strategy = "shared-established-group-recovery";
            }
            if (fitted is null && (!entryFirst || group is not null && !group.VerifiedFloors.Contains(target.Floor)))
            {
                fitted = RecoverGroup("established-group-before-entry", false); strategy = "shared-established-group-recovery";
            }
            if (fitted is null)
            {
                Mark("entry-reverify");
                fitted = ReverifyEntry(frame, target, reference, candidate, options, stageContext, Obsolete, cancellation, Log);
                strategy = "verified-entry-current-frame";
            }
            if (fitted is null && entryFirst)
            {
                fitted = RecoverGroup("established-group-after-entry", false); strategy = "shared-established-group-recovery";
            }
            if (fitted is null)
            {
                Mark("registered-floor");
                fitted = RegisteredFloor(frame, target, reference, group, Obsolete, cancellation, Log);
                strategy = "shared-registered-floor-recovery";
            }
            if (fitted is null)
            {
                Mark("initial"); strategy = "shared-initial-structure";
                if (options.PoseIndexCandidatesEnabled)
                    fitted = services.PoseIndexInitial?.Invoke(stageContext, false) ?? Failure("s0-pose-index-fit-unavailable");
                else fitted = current is null ? Failure("initial-structure-query-unavailable") : S1StructureInitialFit.Evaluate(frame, current, reference.Geometry, Obsolete);
                if (IsSuccess(fitted) && (fitted["coordinateSpace"]?.ToString() != "author-render-to-client" || fitted["routeProfileId"]?.ToString() != "shared-structure"))
                    fitted = Failure("initial-structure-coordinate-mismatch");
            }
            if (Obsolete()) return Done(Failure("structure-work-obsolete"));
            if (!IsSuccess(fitted)) return Done(fitted);
            // This is the real S0 consumer, not metadata stripped by a test:
            // implicit-domain Host fits do not export a selected scan island.
            Mark("canonical-residual");
            var matrix = Matrix(fitted["matrixSourceToClient"]!);
            var residual = CanonicalResidual(frame, reference, matrix);
            if (!ResidualVerified(residual)) return Done(new() { ["success"] = false, ["reason"] = "canonical-current-frame-residual-unverified", ["residual"] = residual });
            bool markerObservation = false;
            if (current is not null && strategy == "shared-fast-alignment")
            {
                if (Flag(fitted, "markerOccludedWallProposal")) markerObservation = (fitted["diagnostics"]?["queryChains"]?.GetValue<int>() ?? 0) == 0;
                else if (fitted["diagnostics"]?["queryChains"] is null)
                {
                    var measured = Geometry.ObservedChains(frame, current);
                    markerObservation = measured.Count == 0 && MarkerProposals(frame, current, measured).Count > 0;
                }
            }
            fitted["selectedRouteCurrentFrameResidual"] = residual;
            fitted["markerOccludedWallObservation"] = markerObservation;
            fitted["observationBounds"] = null; fitted["observationComponent"] = null;
            fitted["coordinateSpace"] = "shared-structure-to-client"; fitted["routeProfileId"] = "shared-structure"; fitted["strategy"] = strategy;
            Log("s1-fit-observation", new() { ["success"] = true, ["reason"] = fitted["reason"]?.DeepClone(), ["strategy"] = strategy });
            if (Flag(fitted, "markerOccludedWallProposal") || markerObservation)
            {
                Mark("marker-peer-rejection");
                var rejected = MarkerPeerRejection(frame, target, reference, matrix, Obsolete);
                if (rejected is not null) return Done(rejected);
            }
            return Done(Obsolete() ? Failure("structure-work-obsolete") : fitted);
        }
        catch (OperationCanceledException) { return Done(Failure("structure-work-obsolete")); }
    }

    private static StructurePrior? ScopedPrior(S0HostPosePrior prior, S0HostFitOptions options, ResourceCatalogEntry target, Prepared reference)
    {
        if (options.OwnerScope is null || !ReferenceEquals(prior.OwnerScope, options.OwnerScope) || prior.Epoch != options.Epoch || !ReferenceEquals(prior.Target, target)) return null;
        var m = prior.Matrix?.Select(row => row?.ToArray() ?? []).ToArray();
        if (m is null || m.Length != 3 || m.Any(row => row.Length != 3 || row.Any(v => !double.IsFinite(v))) || m[0][0] <= 0 ||
            Math.Abs(m[2][0]) > 1e-12 || Math.Abs(m[2][1]) > 1e-12 || Math.Abs(m[2][2] - 1) > 1e-12 ||
            Math.Abs(m[0][1]) > 1e-10 || Math.Abs(m[1][0]) > 1e-10 || Math.Abs(m[1][1] - m[0][0]) > 1e-10) return null;
        return new(reference.Geometry.Source, m);
    }

    private static JsonObject? Fast(Mat frame, Observation? current, Prepared reference, StructurePrior? prior, bool localOnly,
        Func<bool> obsolete, Action<string, JsonObject?> log)
    {
        if (obsolete()) return Failure("structure-work-obsolete");
        if (reference.Fast is null)
        { log("experimental-fast-alignment", new() { ["reason"] = "fast-prepared-unavailable", ["accepted"] = false, ["searchKind"] = localOnly ? "local-prior" : "global-search" }); return null; }
        if (current is null) return null;
        var clock = Stopwatch.StartNew(); var result = FastStructureFit.Evaluate(frame, current, reference.Geometry, reference.Fast, prior, localOnly, obsolete);
        if (obsolete() || result.Reason.Contains("obsolete", StringComparison.Ordinal)) return Failure("structure-work-obsolete");
        var diagnostic = result.Proposal is null ? new JsonObject() : System.Text.Json.JsonSerializer.SerializeToNode(result.Proposal.Diagnostics, DiagnosticJson)!.AsObject();
        diagnostic["pointGeometryReason"] = result.Point?["reason"]?.DeepClone(); diagnostic["wallGeometryReason"] = result.Wall?["reason"]?.DeepClone();
        diagnostic["totalMs"] = clock.Elapsed.TotalMilliseconds; diagnostic["accepted"] = result.GeometryVerified;
        var detail = diagnostic.DeepClone().AsObject(); detail["searchKind"] = localOnly ? "local-prior" : "global-search";
        log("experimental-fast-alignment", detail);
        if (!result.GeometryVerified || result.Matrix is null) return null;
        var residual = CanonicalResidual(frame, reference, result.Matrix, current);
        if (!ResidualVerified(residual)) return null;
        return new() { ["success"] = true, ["reason"] = "experimental-fast-structure", ["matrixSourceToClient"] = Matrix(result.Matrix),
            ["coordinateSpace"] = "shared-structure-to-client", ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = residual,
            ["fastAlignmentDiagnostics"] = diagnostic, ["inliers"] = Geometry.Nodes(current).Count };
    }

    private static bool Flag(JsonNode? row, string key) => row?[key] is JsonValue value && value.TryGetValue<bool>(out bool flag) && flag;
    private static bool IsSuccess(JsonNode? row) => Flag(row, "success");
    private static string Reason(JsonNode? row) => row?["reason"]?.ToString() ?? "";
    private static JsonObject Failure(string reason) => new() { ["success"] = false, ["reason"] = reason };
    private static bool ResourceFailure(Exception error) => error is IOException or ArgumentException or InvalidOperationException or KeyNotFoundException or OpenCVException or System.Text.Json.JsonException;
}
