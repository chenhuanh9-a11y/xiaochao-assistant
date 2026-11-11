using System.Text.Json;
using StarMap.Core.Alignment;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private S0HostFit? _currentResidualHost;
    private BoundResources? _currentResidualResources;

    private RuntimeOperationResult? TryRenewCurrentPose(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string floor, FloorObservationResult read, JsonElement preparation)
    {
        RuntimeRenderPacket previous; CanonicalGrant canonical; CapturedClientFrame previousFrame;
        GeometryGroup? group; RuntimeDisplayState display;bool s1Manual;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if(_selection.Season=="s0"&&(_mapId is null||_identitySource=="group-default"))return null;
            // Selected S1 identities share renewal/Core commit, but measure
            // full-client geometry and never enter S0's group/scale cache lane.
            s1Manual=request.Action=="observe_manual_floor" && _manualAlignmentSession && S1ManualIdentity && S1FloorObserverAvailable;
            if ((!s1Manual && _selection.Season != "s0") || !RenderCurrent() || _canonical is null
                || !s1Manual && (_canonical.Group is null || !ReferenceEquals(_canonical.Group,_geometryGroup) || SteadyEnabled && SteadyMaterialReady)
                || _floor != floor || read.FloorId is { } observed && observed != floor
                || _render!.Client != captured.Context || _render.Stamp.WindowIdentity != captured.Stamp.WindowIdentity
                || _render.Stamp.Generation != captured.Stamp.Generation) return null;
            previous = _render; canonical = _canonical; previousFrame = _frame!; group = _geometryGroup; display = _display;
        }
        var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season,
            bound.Passports.MapProfiles[previous.MapId], previous.MapId, floor);
        if (entry is null) return null;
        if (!s1Manual && (!ReferenceEquals(_currentResidualResources, bound) || _currentResidualHost is null))
        {
            _currentResidualHost?.Dispose();
            _currentResidualHost = new(bound.Catalog, bound.Passports, loadReference: bound.LoadReference);
            _currentResidualResources = bound;
        }
        using var pixels = ToBgr(captured);
        var measured = s1Manual
            ? GetStructureFits(bound).EvaluateCanonicalResidual(pixels,entry,canonical.NativeToClient.ToArray(),()=>Obsolete(epoch,token,captured,bound))
            : _currentResidualHost!.EvaluateCanonicalResidual(pixels, entry, canonical.NativeToClient.ToArray(),()=>Obsolete(epoch,token,captured,bound));
        var residual = ReadResidualEvidence(measured);
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (!ReferenceEquals(previous, _render) || !ReferenceEquals(canonical, _canonical) || !ReferenceEquals(previousFrame, _frame)
                || !ReferenceEquals(group, _geometryGroup) || !RenderCurrent()) return Receipt(request, false, "cancelled-or-stale");
            if (!AlignmentObservationEvidence.RenderedProfileResidualIsVerified(residual)
                || !residual.TryGetValue("coordinatePackId", out var pack) || pack?.ToString() != canonical.PackId)
            { RevokeDisplay("current-frame-residual-failed"); return null; }
            bool accepted = _materialProposal is { Accepted: true } proposal && MaterialProposalCurrent(proposal);
            long generation = _frameGeneration + 1;
            _alignment!.BeginObservation(new(_roundId!), previous.MapId, floor, generation, [captured.Stamp.FrameId, bound.Id]);
            var candidate = new AlignmentCandidate(previous.MapId, floor, new(canonical.Matrix.ToArray()),
                "same-floor-reopen-verified-refresh", generation, confidence: 1, anchorCount: 1, anchorKind: "global",
                currentFrameVerified: true, residualVerified: true, globalResidualVerified: true, translationOnly: true,
                metadata: new Dictionary<string, object?> { ["routeProfileId"] = "shared-structure" });
            var verdict = _alignment.Adjudicate(candidate); var authorization = _alignment.Authorize(verdict);
            if (authorization is null || !_alignment.Commit(authorization, persistPose: false))
            { RevokeDisplay("refresh-core-commit-rejected"); return Receipt(request, false, verdict.Reason); }
            _frameGeneration = generation; _frame = captured; _floor = floor; _probeMisses = 0;
            RememberPointerObservation(captured,read);
            _pose = new(previous.MapId, floor, bound.Id, captured.Stamp.FrameId, canonical.NativeToClient.ToArray());
            _canonical = canonical with { Stamp = captured.Stamp, Authorization = authorization, ProposedCoverage = null };
            _render = new(previous, captured, authorization, generation, _epoch); _display = display;
            // A successful fresh residual creates fresh learnable geometry even
            // when the shared pixels came from a presentation-only author rebase.
            RegisterMaterialProposal(accepted);
            _reason = "current-pose-refreshed";
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "current-pose-renewal", residual = measured,
                floor = read.FloorRead, preparation, unchanged = true }, Json);
            Publish(); return Receipt(request, true, _reason);
        }
    }
}
