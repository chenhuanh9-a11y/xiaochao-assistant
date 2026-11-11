using System.Text.Json;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision;
using StarMap.Vision.Steady;

namespace StarMap.Runtime;

/// <summary>Small serializable render status; pixel ownership stays outside snapshots.</summary>
public sealed record RuntimeDisplayState(bool Prepared, bool CanonicalVerified, bool LiveAuthorized,
    string Reason, string? PreferredProfile = null, string? EffectiveProfile = null, string? DisplayKind = null,
    int SceneFloors = 0, string? SceneReason = null);

/// <summary>Immutable material plus the exact captured-client and core authorization lifetime.</summary>
public sealed class RuntimeRenderPacket
{
    internal RuntimeRenderPacket(PreparedDisplayLayer material, CapturedClientFrame frame, DisplayAuthorization authorization,
        long generation, long epoch, bool remembersGeometry = true, RuntimeSceneFrame? scene = null)
    { Material = material; Stamp = frame.Stamp; Client = frame.Context; Authorization = authorization; Generation = generation; Epoch = epoch; RemembersGeometry = remembersGeometry; Scene=scene; }
    internal RuntimeRenderPacket(RuntimeRenderPacket previous, CapturedClientFrame frame, DisplayAuthorization authorization,
        long generation, long epoch) : this(previous.Material, frame, authorization, generation, epoch, scene: previous.Scene)
    { MaterialToken = previous.MaterialToken; }
    // A separate material ACK intent over the same observation and geometry.
    internal RuntimeRenderPacket(RuntimeRenderPacket previous)
    {
        Material=previous.Material;Scene=previous.Scene;Stamp=previous.Stamp;
        Client=previous.Client;Authorization=previous.Authorization;Generation=previous.Generation;Epoch=previous.Epoch;
        RemembersGeometry=previous.RemembersGeometry;PresentationOffsetX=previous.PresentationOffsetX;
        PresentationOffsetY=previous.PresentationOffsetY;TransformRevision=previous.TransformRevision;IsTransient=previous.IsTransient;
    }
    internal RuntimeRenderPacket(RuntimeRenderPacket previous,double offsetX,double offsetY,long revision) : this(previous)
    {
        MaterialToken=previous.MaterialToken;
        RemembersGeometry=false;PresentationOffsetX=offsetX;PresentationOffsetY=offsetY;TransformRevision=revision;IsTransient=true;
    }
    public PreparedDisplayLayer Material { get; }
    public RuntimeSceneFrame? Scene { get; }
    public string MaterialToken { get; } = Guid.NewGuid().ToString("N");
    public CaptureStamp Stamp { get; }
    public string MapId => Authorization.MapId;
    public ClientFrameContext? Client { get; }
    public long Generation { get; }
    public double PresentationOffsetX { get; }
    public double PresentationOffsetY { get; }
    public long TransformRevision { get; }
    public bool IsTransient { get; }
    public bool ProjectionOnly => Authorization.Source=="shared-root-provisional";
    public DisplayAffine PresentationTransform => new(1,0,PresentationOffsetX,0,1,PresentationOffsetY);
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

    private bool CanonicalRenderBindingCurrent() => CanonicalCurrent() && _render is { } render && render.Epoch==_epoch
        && render.Stamp==_canonical!.Stamp && ReferenceEquals(render.Authorization,_canonical.Authorization);

    private bool RenderCurrent()
    {
        if(!_display.Prepared || _render is not { } render || render.Epoch!=_epoch)return false;
        if(CanonicalRenderBindingCurrent())return true;
        return _projection is { } projection && DragCycleCurrent && _dragCycle!.Current==projection.Provenance
            && ProjectionGrantCurrent(projection) && render.Stamp==projection.Frame.Stamp
            && ReferenceEquals(render.Authorization,projection.Render.Authorization);
    }

    private sealed record CanonicalGrant(CaptureStamp Stamp, long Epoch, DisplayAuthorization Authorization,
        string PackId, DisplayAffine Matrix, SharedStructureData Reference,
        SharedStructureData NativeReference, DisplayAffine NativeToClient, SteadyCoverageState? ProposedCoverage, GeometryGroup? Group);

    private sealed record PreparedRuntimeDisplay(DisplaySelectionResult Selection, DisplayMaterialResult Material,
        DisplayBridgeResult? CanonicalBasis, SharedStructureData NativeReference, DisplayAffine NativeToClient,
        SteadyCoverageState? ProposedCoverage = null, RuntimeSceneSources? Scene = null);

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

    private (bool Success,string Reason) RefreshDisplayPreference(long epoch,CancellationToken token,string preferred,
        ProjectionPreferenceSource? projectionSource)
    {
        BoundResources bound;CapturedClientFrame? frame;GeometryGroup? group;CanonicalGrant? previous;
        RuntimeRenderPacket? source;MapDragCycle? cycle;SessionSelection selection;long intent;string map,floor;
        SharedStructureData? reference;DisplayAffine native;
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            if(!_roundActive || _resources is null || EffectiveMapId is not { } selectedMap || _floor is not { } selectedFloor)
                return (true,"context-bound");
            bound=_resources;frame=_frame;group=_geometryGroup;cycle=_dragCycle;selection=_selection;intent=_groupIntent;
            source=_render;map=selectedMap;floor=selectedFloor;
            previous=CanonicalCurrent()?_canonical:null;
            reference=projectionSource?.Grant.NativeReference??previous?.NativeReference;
            native=projectionSource?.Grant.NativeToClient??previous?.NativeToClient??DisplayAffine.Identity;
        }
        try
        {
            RequireOwner();
            var entry=bound.Catalog.Find(bound.Passports.Mode,bound.Passports.Season,bound.Passports.MapProfiles[map],map,floor);
            if(entry is null)return Fail("route-display-reprojection-failed:target-resource-unavailable");
            reference??=bound.LoadReference(entry).Value;
            if(reference is null)return Fail("route-display-reprojection-failed:target-reference-unavailable");
            var prepared=PrepareDisplay(bound,entry,native.ToArray(),reference,preferred,token);
            bool current=projectionSource is not null || previous is not null;
            var expected=projectionSource?.Render.Material.Bridge.CanonicalToClient??previous?.Matrix;
            string? pack=projectionSource?.Render.Material.Bridge.CanonicalPackId??previous?.PackId;
            bool basisMatches=prepared.CanonicalBasis is { } basis && basis.CanonicalPackId==pack
                && basis.CanonicalToClient is { } canonical && expected is { } matrix && canonical.AlmostEquals(matrix);
            if(current && !basisMatches)return Fail("display-preference-canonical-basis-changed");
            if(prepared.Material.Layer is not { } layer)
            {
                if(previous is null)return Fail("route-display-reprojection-failed:"+prepared.Material.Reason);
                lock(_sync)
                {
                    RequireOwner();
                    // A missing display option cannot erase a separately verified native pose.
                    _render=null;_materialProposal=null;
                    _display=new(false,true,false,prepared.Material.Reason,preferred,prepared.Selection.EffectiveProfileId,prepared.Selection.DisplayKind);
                    _reason="display-preference-material-unavailable";Publish();return (true,_reason);
                }
            }
            if(current)prepared=prepared with {Scene=PrepareMapScene(bound,entry,prepared,token)};
            var rebase=PrepareDisplayRebase(bound,group,preferred,token);
            lock(_sync)
            {
                RequireOwner();
                group?.Model.RebaseDisplayProfile(prepared.Selection.EffectiveProfileId,rebase.Previous,rebase.Next);
                _materialProposal=null;
                if(current)
                {
                    var authorization=projectionSource?.Render.Authorization??previous!.Authorization;
                    _render=new(layer,frame!,authorization,_frameGeneration,_epoch,remembersGeometry:false,scene:ProjectMapScene(prepared,floor));
                    PreserveDragPresentation();
                    _display=new(true,previous is not null,_render.IsLive,projectionSource is null?"display-prepared":"shared-root-provisional",
                        preferred,prepared.Selection.EffectiveProfileId,prepared.Selection.DisplayKind,prepared.Scene?.Floors.Count??0,
                        prepared.Scene is null?"common-space-source-unavailable":prepared.Scene.Unavailable.Count==0?"common-scene-ready":"common-scene-partial");
                    if(projectionSource is not null)
                    {
                        _projection=projectionSource.Grant with {Render=_render,Selection=selection,Display=_display};
                        _canonical=null;_pose=null;
                    }
                }
                else
                {
                    // Static author preparation never borrows a group root or a pending Core for display.
                    _projection?.Core.Close(reason:"display-preference-source-unavailable");_projection=null;_render=null;
                    _display=new(false,CanonicalCurrent(),false,"display-preference-static",preferred,
                        prepared.Selection.EffectiveProfileId,prepared.Selection.DisplayKind);
                }
                _reason="route-display-reprojected";Publish();return (true,_reason);
            }
        }
        catch(OperationCanceledException){return (false,"stale-display-settings");}
        catch(Exception error){return Fail("route-display-reprojection-failed:"+error.Message);}

        bool Owned()
        {
            if(_closed || _epoch!=epoch || _selection!=selection || !ReferenceEquals(_resources,bound) || !ReferenceEquals(_frame,frame)
                || !ReferenceEquals(_geometryGroup,group) || !ReferenceEquals(_dragCycle,cycle) || _groupIntent!=intent || !ReferenceEquals(_render,source))return false;
            if(projectionSource is not null)return ReferenceEquals(_projection,projectionSource.Grant) && ReferenceEquals(source,projectionSource.Render)
                && ReferenceEquals(cycle,projectionSource.Cycle) && cycle!.Current==projectionSource.Grant.Provenance && ProjectionBindingCurrent(projectionSource.Grant);
            return previous is not null?ReferenceEquals(_canonical,previous) && CanonicalCurrent():!RenderCurrent();
        }
        void RequireOwner()
        {lock(_sync){RequireCurrent(epoch,token,bound:bound);if(!Owned())throw new OperationCanceledException("stale-display-settings",token);}}
        (bool Success,string Reason) Fail(string reason)
        {
            lock(_sync)
            {
                if(!Owned())return (false,"stale-display-settings");
                // Display preparation must not revoke the accepted observation or saved preference.
                WithdrawDisplay(reason);_pose=null;_reason=reason;Publish();return (false,reason);
            }
        }
    }

    private (Dictionary<string,Matrix3?> Previous,Dictionary<string,Matrix3?> Next) PrepareDisplayRebase(
        BoundResources bound,GeometryGroup? group,string preferred,CancellationToken token)
    {
        var previous=new Dictionary<string,Matrix3?>(StringComparer.Ordinal);var next=new Dictionary<string,Matrix3?>(StringComparer.Ordinal);
        if(group is null)return (previous,next);
        foreach(string floor in group.Model.TrackingByFloor.Keys)
        { previous[floor]=Read(group.Model.RouteProfileId,floor);next[floor]=Read(preferred,floor); }
        return (previous,next);
        Matrix3? Read(string profile,string floor)
        {
            token.ThrowIfCancellationRequested();
            try
            {
                var entry=bound.Catalog.Find(bound.Passports.Mode,bound.Passports.Season,bound.Passports.MapProfiles[group.Model.MapId],group.Model.MapId,floor);
                var reference=entry is null?null:bound.LoadReference(entry).Value;
                if(entry is null || reference is null)return null;
                var selection=new DisplayLayerSelector(new OpenCvDisplayRasterValidator()).Select(bound.Catalog,entry,profile,DisplayAffine.Identity);
                return bound.DisplayMaterials.Prepare(selection,reference,token).Layer?.Bridge.DisplayToReference is { } bridge?new(bridge.ToArray()):null;
            }
            catch(Exception error) when(error is IOException or ArgumentException or InvalidOperationException or KeyNotFoundException){return null;}
        }
    }

    private sealed record CanonicalDisplayCommit(AlignmentCoreV2? Alignment, CanonicalGrant? Grant,
        RuntimeRenderPacket? Render, RuntimeDisplayState Display, GeometryGroup? Group = null);

    private CanonicalDisplayCommit PrepareCanonicalCommit(PreparedRuntimeDisplay prepared,
        IReadOnlyDictionary<string, object?> residual, CapturedClientFrame frame, string map, string floor,
        AlignmentCoreV2? alignment = null,long? observationGeneration=null)
    {
        long generation=observationGeneration??_frameGeneration;
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
        bool needsGroup=_selection.Season=="s0"&&!IsStructureProvisional&&frame.Context is not null;
        var stagedGroup = needsGroup
            ? StageGeometryGroup(prepared, residual, frame, map, floor, canonical) : null;
        if (needsGroup && stagedGroup is null)
            return new(alignment, null, null, new(false, false, false, "round-group-pose-commit-rejected"));
        alignment ??= new(floors: _resources!.Passports.Floors);
        var priorGroup = !IsStructureProvisional&&GeometryGroupCurrent(_geometryGroup, frame, _resources!, map) ? _geometryGroup : null;
        SeedGroupCore(alignment, priorGroup);
        alignment.BeginObservation(new(_roundId!), map, floor, generation, [frame.Stamp.FrameId, _resources!.Id]);
        var candidate = new AlignmentCandidate(map, floor, new(canonical.ToArray()), "initial-structure-v2", generation,
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
        var render = layer is null ? null : new RuntimeRenderPacket(layer, frame, authorization, generation, _epoch,
            scene: ProjectMapScene(prepared,floor));
        return new(alignment, grant, render, new(layer is not null, true, render?.IsLive == true,
            layer is not null ? "display-prepared" : prepared.Material.Reason, selection.PreferredProfileId,
            selection.EffectiveProfileId, selection.DisplayKind, prepared.Scene?.Floors.Count??0,
            prepared.Scene is null?"common-space-source-unavailable":prepared.Scene.Unavailable.Count==0?"common-scene-ready":"common-scene-partial"), stagedGroup);
    }

    private void ApplyCanonicalCommit(CanonicalDisplayCommit commit)
    {
        ClearDragPresentation();
        _alignment = commit.Alignment; _canonical = commit.Grant; _render = commit.Render; _display = commit.Display;
        _geometryGroup = commit.Group;
        RegisterMaterialProposal();
    }

    private void RevokeDisplay(string reason)
    { WithdrawDisplay(reason);_frameGeneration++; }

    private void WithdrawDisplay(string reason)
    {
        DiscardGroupMaterial(reason);
        ClearProjectionDisplay(reason);
        _alignment?.Close(reason: reason); _render = null; _canonical = null; _materialProposal = null;
        _display = new(false, false, false, reason);
    }
}
