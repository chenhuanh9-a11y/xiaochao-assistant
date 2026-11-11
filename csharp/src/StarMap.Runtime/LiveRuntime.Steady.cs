using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision.Steady;

namespace StarMap.Runtime;

public sealed record RenderMaterialAcknowledgement(bool Accepted, bool NeedsWorker, string Reason);

public sealed partial class LiveRuntime
{
    private sealed record SteadyFloorKey(string Map, string Floor, string Resource, string Window,
        CaptureEvidenceKind Evidence, int Width, int Height, int Left, int Top, uint Dpi);
    private sealed record CommittedSteadyFloor(RoundLayerGroup Owner, SharedStructureData Reference, double Scale,
        SteadyCoverageState? Coverage);
    private sealed record PreparedSteadyFit(JsonObject Fit, PreparedRuntimeDisplay Display);
    private readonly Dictionary<SteadyFloorKey, CommittedSteadyFloor> _steadyFloors = new();
    private string? _rememberedMaterial;
    private RoundLayerGroup? _steadyOwner;
    private bool _steadySettingEnabled;
    private sealed class MaterialRememberProposal(RuntimeRenderPacket packet, CapturedClientFrame frame,
        CanonicalGrant canonical, BoundResources resources, GeometryGroup group, RuntimeContext context, long epoch)
    {
        internal readonly RuntimeRenderPacket Packet = packet;
        internal readonly CapturedClientFrame Frame = frame;
        internal readonly CanonicalGrant Canonical = canonical;
        internal readonly BoundResources Resources = resources;
        internal readonly GeometryGroup Group = group;
        internal readonly RuntimeContext Context = context;
        internal readonly long Epoch = epoch;
        internal bool Accepted, Consumed;
    }
    private MaterialRememberProposal? _materialProposal;

    // Original Qt S1 supplies an explicit full-frame observation domain. The
    // Host deliberately skips 0059 there, even when the setting is enabled.
    private bool SteadyEnabled => _selection.Season == "s0" && !IsStructureProvisional && _steadySettingEnabled
        && Environment.GetEnvironmentVariable("IDENTITYV_IDVB0059_STEADY") != "0";
    private bool SteadyMaterialReady => _geometryGroup is { } group && ReferenceEquals(_steadyOwner, group.Model)
        && _steadyFloors.Values.Any(row => ReferenceEquals(row.Owner, group.Model));

    public bool SetIdvb0059Enabled(bool enabled)
    { lock (_sync) { if (_closed) return false; _steadySettingEnabled = enabled; return true; } }

    private static SteadyFloorKey? SteadyKey(CapturedClientFrame frame, string map, string floor, string resource)
    {
        if (frame.Context is not { } context) return null;
        var g = context.Geometry;
        return new(map, floor, resource, frame.Stamp.WindowIdentity, context.EvidenceKind,
            frame.Width, frame.Height, g.Left, g.Top, g.Dpi);
    }

    private void ResetSteady()
    { _steadyFloors.Clear(); _rememberedMaterial = null; _steadyOwner = null; _materialProposal = null; }

    private void RegisterMaterialProposal(bool accepted = false)
    {
        _materialProposal = _selection.Season == "s0" && _render is { RemembersGeometry: true } packet
            && _frame is { Context: not null } frame && _canonical is { Group: { } group } canonical
            && ReferenceEquals(group, _geometryGroup) && _resources is { } resources
            ? new(packet, frame, canonical, resources, group, Context(), _epoch) { Accepted = accepted } : null;
    }

    private bool MaterialProposalCurrent(MaterialRememberProposal proposal) => proposal.Epoch == _epoch
        && _activeDrag is null && !DragCycleCurrent
        && ReferenceEquals(proposal.Packet, _render) && ReferenceEquals(proposal.Frame, _frame)
        && ReferenceEquals(proposal.Canonical, _canonical) && ReferenceEquals(proposal.Resources, _resources)
        && ReferenceEquals(proposal.Group, _geometryGroup) && proposal.Context == Context() && RenderCurrent();

    public RenderMaterialAcknowledgement AcknowledgeRenderMaterial(RuntimeRenderPacket offered)
    {
        ArgumentNullException.ThrowIfNull(offered);
        lock (_sync)
        {
            if (_materialProposal is not { } proposal || !MaterialProposalCurrent(proposal)
                || !proposal.Packet.SharesMaterial(offered) || proposal.Packet.Epoch != offered.Epoch
                || proposal.Packet.MapId != offered.MapId || proposal.Packet.Client != offered.Client
                || proposal.Packet.Stamp.WindowIdentity != offered.Stamp.WindowIdentity || proposal.Packet.Stamp.Generation != offered.Stamp.Generation)
                return new(false, false, "material-context-expired");
            bool first = !proposal.Accepted; proposal.Accepted = true;
            return new(first, !proposal.Consumed, first ? "material-accepted" : "material-already-accepted");
        }
    }

    /// <summary>Unconsumed current proposal only; it never proposes identity or a new transform.</summary>
    public RuntimeRenderPacket? ReadMaterialToRemember()
    {
        lock (_sync) return _materialProposal is { Consumed: false } proposal && MaterialProposalCurrent(proposal) ? proposal.Packet : null;
    }

    // Uses the existing private Session work lane. This method cannot publish
    // recognition state, overwrite a pose or grant display/live permission.
    public async ValueTask RememberRenderMaterialAsync(string renderToken, RuntimeContext lifetime, CancellationToken cancellationToken)
    {
        await _worker.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            CapturedClientFrame frame; CanonicalGrant canonical; BoundResources bound; SteadyFloorKey key;
            RoundLayerGroup owner; MaterialRememberProposal proposal; SteadyCoverageState? previous;
            lock (_sync)
            {
                if (_materialProposal is not { Accepted: true, Consumed: false } current || !MaterialProposalCurrent(current)
                    || current.Packet.MaterialToken != renderToken || !SameMaintenanceLifetime(lifetime, current.Context)) return;
                proposal = current; proposal.Consumed = true;
                // ACK/consumption are independent of whether this setting permits
                // a cache write. Existing ready cache remains valid when disabled.
                if (!SteadyEnabled) return;
                frame = proposal.Frame; canonical = proposal.Canonical; bound = proposal.Resources;
                if (!proposal.Group.Model.CanonicalGeometryVerifiedFloors.Contains(_floor!)) return;
                owner = proposal.Group.Model;
                var scoped = SteadyKey(frame, _mapId!, _floor!, bound.Id);
                var matrix = canonical.NativeToClient;
                if (scoped is null || !matrix.IsFinite || Math.Abs(matrix.M01) > 1e-8 || Math.Abs(matrix.M10) > 1e-8
                    || Math.Abs(matrix.M11 - matrix.M00) > 1e-8) return;
                key = scoped;
                previous = _steadyFloors.TryGetValue(key, out var row) && ReferenceEquals(row.Owner, owner)
                    && ReferenceEquals(row.Reference, canonical.NativeReference) ? row.Coverage : null;
            }
            cancellationToken.ThrowIfCancellationRequested();
            SteadyCoverageState? coverage = canonical.ProposedCoverage;
            if (coverage is null)
            {
                using var pixels = ToBgr(frame);
                try { coverage = Idvb0059Steady.ObserveCoverage(pixels, canonical.NativeReference, canonical.NativeToClient.ToArray(), previous); }
                catch (Exception error) when (error is ArgumentException or InvalidDataException or OpenCVException)
                { coverage = previous; }
            }
            lock (_sync)
            {
                if (_closed || !SteadyEnabled || cancellationToken.IsCancellationRequested || !ReferenceEquals(_materialProposal, proposal)
                    || !MaterialProposalCurrent(proposal)) return;
                if (!ReferenceEquals(_steadyOwner, owner)) _steadyFloors.Clear();
                _steadyOwner = owner;
                _steadyFloors[key] = new(owner, canonical.NativeReference, canonical.NativeToClient.M00, coverage);
                _rememberedMaterial = renderToken;
            }
        }
        finally { ReleaseRuntimeWorker(); }
    }

    // A queued cache intent names material, not the frame that happened to be
    // displayed when queued. Only the sealed current proposal supplies geometry.
    private static bool SameMaintenanceLifetime(RuntimeContext queued, RuntimeContext current) =>
        queued.RoundId == current.RoundId && queued.ResourceId == current.ResourceId && queued.Selection == current.Selection;

    private PreparedSteadyFit? TrySteady(long epoch, CancellationToken token, CapturedClientFrame captured,
        BoundResources bound, ResourceCatalogEntry entry, SharedStructureData reference, Mat frame,
        string preferred, string? expectedPack, out JsonObject diagnostic)
    {
        CommittedSteadyFloor? row;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            var key = SteadyKey(captured, entry.MapId, entry.Floor, bound.Id);
            row = key is not null ? _steadyFloors.GetValueOrDefault(key) : null;
            if (!SteadyEnabled)
            { diagnostic = new() { ["attempted"] = false, ["reason"] = "steady-disabled" }; return null; }
            if (row is null || !GeometryGroupCurrent(_geometryGroup, captured, bound, entry.MapId)
                || !ReferenceEquals(row.Owner, _geometryGroup!.Model)
                || !_geometryGroup.Model.CanonicalGeometryVerifiedFloors.Contains(entry.Floor)
                || !ReferenceEquals(row.Reference, reference))
            { diagnostic = new() { ["attempted"] = false, ["reason"] = "trusted-target-floor-scale-unavailable" }; return null; }
        }
        var started = Stopwatch.GetTimestamp();
        var solved = Idvb0059Steady.Solve(frame, reference, row.Scale, row.Coverage,
            cancelled: () => Obsolete(epoch, token, captured, bound));
        diagnostic = new() { ["attempted"] = true, ["accepted"] = solved.Accepted, ["reason"] = solved.Reason,
            ["trustedScale"] = row.Scale, ["diagnostics"] = solved.Diagnostics.DeepClone() };
        CheckCurrent(epoch, token, captured, bound);
        if (!solved.Accepted || solved.MatrixSourceToClient is not { Length: 2 } matrix) return null;
        double[][] native = [matrix[0].ToArray(), matrix[1].ToArray(), [0, 0, 1]];
        var display = PrepareDisplay(bound, entry, native, reference, preferred, token) with { ProposedCoverage = solved.ProposedCoverage };
        var basis = display.CanonicalBasis;
        // This is the actual validated static reference/canonical bridge, not an
        // author-image match or a fabricated validation-spec acknowledgement.
        if (basis?.Accepted != true || basis.CanonicalToClient is null || string.IsNullOrEmpty(basis.CanonicalPackId)
            || expectedPack is not null && basis.CanonicalPackId != expectedPack)
        {
            diagnostic["accepted"] = false; diagnostic["reason"] = "steady-canonical-spec-unavailable"; return null;
        }
        var residual = AlignmentObservationEvidence.CanonicalResidualEvidence(
            new Dictionary<string, object?> { ["verified"] = true }, "game-rendered-shared-structure", basis.CanonicalPackId);
        var evidence = JsonSerializer.SerializeToNode(residual)!.AsObject();
        evidence["verificationMethod"] = "idvb0059-vpsg3-current-frame";
        evidence["idvb0059"] = solved.Diagnostics.DeepClone();
        var fit = new JsonObject { ["success"] = true, ["reason"] = "idvb0059-steady",
            ["matrixSourceToClient"] = JsonSerializer.SerializeToNode(native), ["coordinateSpace"] = "shared-structure-to-client",
            ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = evidence,
            ["idvb0059Diagnostics"] = solved.Diagnostics.DeepClone(), ["idvb0059Coverage"] = solved.ProposedCoverage?.Snapshot(),
            ["totalMs"] = Stopwatch.GetElapsedTime(started).TotalMilliseconds };
        return new(fit, display);
    }
}
