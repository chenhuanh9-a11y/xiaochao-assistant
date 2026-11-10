using System.Text.Json;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;
using StarMap.Vision.Steady;

namespace StarMap.Runtime;

/// <summary>Small serializable render status; pixel ownership stays outside snapshots.</summary>
public sealed record RuntimeDisplayState(bool Prepared, bool CanonicalVerified, bool LiveAuthorized,
    string Reason, string? PreferredProfile = null, string? EffectiveProfile = null, string? DisplayKind = null);

/// <summary>Immutable material plus the exact captured-client and core authorization lifetime.</summary>
public sealed class RuntimeRenderPacket
{
    internal RuntimeRenderPacket(PreparedDisplayLayer material, CapturedClientFrame frame, DisplayAuthorization authorization,
        long generation, long epoch, bool remembersGeometry = true)
    { Material = material; Stamp = frame.Stamp; Client = frame.Context; Authorization = authorization; Generation = generation; Epoch = epoch; RemembersGeometry = remembersGeometry; }
    internal RuntimeRenderPacket(RuntimeRenderPacket previous, CapturedClientFrame frame, DisplayAuthorization authorization,
        long generation, long epoch) : this(previous.Material, frame, authorization, generation, epoch)
    { MaterialToken = previous.MaterialToken; }
    public PreparedDisplayLayer Material { get; }
    public string MaterialToken { get; } = Guid.NewGuid().ToString("N");
    public CaptureStamp Stamp { get; }
    public string MapId => Authorization.MapId;
    public ClientFrameContext? Client { get; }
    public long Generation { get; }
    internal long Epoch { get; }
    internal bool RemembersGeometry { get; }
    internal DisplayAuthorization Authorization { get; }
    public bool IsLive => Client?.EvidenceKind == CaptureEvidenceKind.LiveClient;
    /// <summary>Unchanged pixels, not an observation or display permission.</summary>
    public bool SharesMaterial(RuntimeRenderPacket other) => MaterialToken == other.MaterialToken && ReferenceEquals(Material, other.Material);
}

public sealed partial class LiveRuntime
{
    private AlignmentCoreV2? _alignment;
    private RuntimeRenderPacket? _render;
    private CanonicalGrant? _canonical;
    private long _frameGeneration;
    private RuntimeDisplayState _display = new(false, false, false, "pose-not-verified");

    // This read performs bounded lifetime checks only. Resource hashing/decoding
    // belongs to the worker, never the UI pulse or the overlay reader.
    public bool TryReadRender(out RuntimeRenderPacket? packet)
    {
        lock (_sync)
        {
            packet = RenderCurrent() ? _render : null;
            return packet is not null;
        }
    }

    private bool CanonicalCurrent() => !_closed && _mapOpen && _frame is not null && _canonical is { } grant
        && grant.Epoch == _epoch && grant.Stamp == _frame.Stamp && _pose is not null
        && _capture.IsObservationContextCurrent(_frame) && _alignment?.ActiveObservation is { } observation
        && grant.Authorization.Current(observation.ObservationId, new(_roundId!), EffectiveMapId!, _floor!, _frameGeneration);

    private bool RenderCurrent() => CanonicalCurrent() && _render is { } render && render.Epoch == _epoch
        && render.Stamp == _canonical!.Stamp && ReferenceEquals(render.Authorization, _canonical.Authorization);

    private sealed record CanonicalGrant(CaptureStamp Stamp, long Epoch, DisplayAuthorization Authorization,
        string PackId, DisplayAffine Matrix, SharedStructureData Reference,
        SharedStructureData NativeReference, DisplayAffine NativeToClient, SteadyCoverageState? ProposedCoverage, GeometryGroup? Group);

    private sealed record PreparedRuntimeDisplay(DisplaySelectionResult Selection, DisplayMaterialResult Material,
        DisplayBridgeResult? CanonicalBasis, SharedStructureData NativeReference, DisplayAffine NativeToClient,
        SteadyCoverageState? ProposedCoverage = null);

    private PreparedRuntimeDisplay PrepareDisplay(BoundResources bound, ResourceCatalogEntry entry, double[][] matrix,
        SharedStructureData fittedReference, string preferred, CancellationToken token)
    {
        if (!DisplayAffine.TryRead(JsonSerializer.SerializeToElement(matrix), out var referenceToClient))
            throw new InvalidDataException("display-fit-affine-unavailable");
        var validator = new OpenCvDisplayRasterValidator();
        var selection = new DisplayLayerSelector(validator).Select(bound.Catalog, entry, preferred, referenceToClient);
        var preparer = bound.DisplayMaterials;
        var material = preparer.Prepare(selection, fittedReference, token);
        // Missing custom/author pixels cannot erase a successful geometric fit.
        // Validate its exact fitted reference independently of display preference.
        var basis = material.Layer?.Bridge;
        if (basis is null)
        {
            var neutral = new DisplayBridgeResolver(validator).ResolveDisplayReference(bound.Catalog, entry,
                "shared-structure", referenceToClient, neutral: true);
            basis = preparer.Prepare(neutral, fittedReference, token).Layer?.Bridge;
        }
        return new(selection, material, basis, fittedReference, referenceToClient);
    }

    private static IReadOnlyDictionary<string, object?> ReadResidualEvidence(System.Text.Json.Nodes.JsonNode residual)
    {
        using var json = JsonDocument.Parse(residual.ToJsonString());
        return json.RootElement.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.ValueKind switch
        {
            JsonValueKind.True => (object?)true, JsonValueKind.False => false, JsonValueKind.String => p.Value.GetString(),
            JsonValueKind.Null => null, JsonValueKind.Number => p.Value.GetDouble(), _ => p.Value.Clone()
        }, StringComparer.Ordinal);
    }

    private bool RefreshDisplayPreference(long epoch, CancellationToken token, string preferred)
    {
        BoundResources bound; CapturedClientFrame frame; NativeGeometryPose pose; CanonicalGrant previous;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!CanonicalCurrent()) return false;
            bound = _resources!; frame = _frame!; pose = _pose!; previous = _canonical!;
        }
        var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season,
            bound.Passports.MapProfiles[pose.MapId], pose.MapId, pose.FloorId);
        if (entry is null) return false;
        var prepared = PrepareDisplay(bound, entry, pose.ToMatrix(), previous.NativeReference, preferred, token);
        lock (_sync)
        {
            RequireCurrent(epoch, token, frame, bound);
            if (!ReferenceEquals(_canonical, previous) || !CanonicalCurrent()) return false;
            var layer = prepared.Material.Layer;
            _materialProposal = null;
            // Preference changes replace pixels, never the canonical pose/token.
            if (prepared.CanonicalBasis is not { } basis || basis.CanonicalPackId != previous.PackId
                || basis.CanonicalToClient is not { } canonical || !canonical.AlmostEquals(previous.Matrix))
            {
                RevokeDisplay("display-preference-canonical-basis-changed");
            }
            else if (layer is null)
            {
                _render = null;
                _display = new(false, true, false, prepared.Material.Reason, preferred,
                    prepared.Selection.EffectiveProfileId, prepared.Selection.DisplayKind);
            }
            else
            {
                _geometryGroup?.Model.RebaseDisplayProfile(prepared.Selection.EffectiveProfileId, pose.FloorId, GroupPixels(layer));
                _render = new(layer, frame, previous.Authorization, _frameGeneration, _epoch, remembersGeometry: false);
                _display = new(true, true, _render.IsLive, "display-prepared", preferred,
                    prepared.Selection.EffectiveProfileId, prepared.Selection.DisplayKind);
            }
            bool reprojected = _render is not null && ReferenceEquals(_render.Authorization, previous.Authorization);
            if (reprojected) _reason = "route-display-reprojected";
            Publish(); return reprojected;
        }
    }

    private sealed record CanonicalDisplayCommit(AlignmentCoreV2? Alignment, CanonicalGrant? Grant,
        RuntimeRenderPacket? Render, RuntimeDisplayState Display, GeometryGroup? Group = null);

    private CanonicalDisplayCommit PrepareCanonicalCommit(PreparedRuntimeDisplay prepared,
        IReadOnlyDictionary<string, object?> residual, CapturedClientFrame frame, string map, string floor,
        AlignmentCoreV2? alignment = null)
    {
        var selection = prepared.Selection;
        var layer = prepared.Material.Layer;
        var bridge = prepared.CanonicalBasis;
        if (bridge?.CanonicalToClient is not { } canonical || string.IsNullOrEmpty(bridge.CanonicalPackId)
            || !residual.TryGetValue("coordinatePackId", out var pack) || pack?.ToString() != bridge.CanonicalPackId
            || !AlignmentObservationEvidence.RenderedProfileResidualIsVerified(residual))
        {
            return new(alignment, null, null,
                new(false, false, false, layer is null ? prepared.Material.Reason : "canonical-fit-basis-mismatch",
                    selection.PreferredProfileId, selection.EffectiveProfileId, selection.DisplayKind));
        }
        var stagedGroup = _selection.Season == "s0" && frame.Context is not null
            ? StageGeometryGroup(prepared, residual, frame, map, floor, canonical) : null;
        if (_selection.Season == "s0" && frame.Context is not null && stagedGroup is null)
            return new(alignment, null, null, new(false, false, false, "round-group-pose-commit-rejected"));
        alignment ??= new(floors: _resources!.Passports.Floors);
        var priorGroup = GeometryGroupCurrent(_geometryGroup, frame, _resources!, map) ? _geometryGroup : null;
        SeedGroupCore(alignment, priorGroup);
        alignment.BeginObservation(new(_roundId!), map, floor, _frameGeneration, [frame.Stamp.FrameId, _resources!.Id]);
        var candidate = new AlignmentCandidate(map, floor, new(canonical.ToArray()), "initial-structure-v2", _frameGeneration,
            currentFrameVerified: true, residualVerified: true, globalResidualVerified: true,
            metadata: new Dictionary<string, object?> { ["routeProfileId"] = "shared-canonical", ["independentFloorAnchor"] = true });
        var verdict = alignment.Adjudicate(candidate);
        var authorization = alignment.Authorize(verdict);
        if (authorization is null || !alignment.Commit(authorization))
        {
            alignment.RevokeDisplay("runtime-canonical-commit-rejected");
            return new(alignment, null, null, new(false, false, false, verdict.Reason));
        }
        var grant = new CanonicalGrant(frame.Stamp, _epoch, authorization, bridge.CanonicalPackId, canonical, bridge.Reference!,
            prepared.NativeReference, prepared.NativeToClient, prepared.ProposedCoverage, stagedGroup);
        SeedGroupCore(alignment, stagedGroup);
        var render = layer is null ? null : new RuntimeRenderPacket(layer, frame, authorization, _frameGeneration, _epoch);
        return new(alignment, grant, render, new(layer is not null, true, render?.IsLive == true,
            layer is not null ? "display-prepared" : prepared.Material.Reason, selection.PreferredProfileId,
            selection.EffectiveProfileId, selection.DisplayKind), stagedGroup);
    }

    private void ApplyCanonicalCommit(CanonicalDisplayCommit commit)
    {
        _alignment = commit.Alignment; _canonical = commit.Grant; _render = commit.Render; _display = commit.Display;
        _geometryGroup = commit.Group;
        RegisterMaterialProposal();
    }

    private void RevokeDisplay(string reason)
    {
        DiscardGroupMaterial(reason);
        _alignment?.Close(reason: reason); _render = null; _canonical = null; _materialProposal = null; _frameGeneration++;
        _display = new(false, false, false, reason);
    }
}
