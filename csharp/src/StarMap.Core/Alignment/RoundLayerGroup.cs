using System.Collections.ObjectModel;

namespace StarMap.Core.Alignment;

/// <summary>One root pose and explicit static floor adapters. No live scale-derived relation learning.</summary>
public sealed class RoundLayerGroup
{
    private readonly object sync=new();
    private Matrix3 root;
    private readonly Dictionary<string,Matrix3> relations=new(StringComparer.Ordinal);
    private readonly Dictionary<string,Dictionary<string,object?>> relationMetadata=new(StringComparer.Ordinal);
    private Dictionary<string,object?> tracking=[];
    private readonly Dictionary<string,Dictionary<string,object?>> trackingByFloor=new(StringComparer.Ordinal);
    private Array? overlay;
    private readonly HashSet<string> verified=[],renderVerified=[],canonicalVerified=[],sourceVerified=[],provisional=[];
    private object?[] displaySignature=[];
    public RoundLayerGroup(string mapId,string routeProfileId,string anchorFloor,Matrix3 rootPose,
        IReadOnlyDictionary<string,Matrix3>? relations=null,IReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>>? relationMetadata=null,
        long generation=0,AlignmentCycleId? cycleId=null,string source="fresh-alignment",string poseOrigin="generic",int[]? poseClientSize=null,
        IEnumerable<object?>? poseDisplaySignature=null,string? trackingFloor=null,IReadOnlyDictionary<string,object?>? tracking=null,
        IReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>>? trackingByFloor=null,string? displayedFloor=null,Array? displayedOverlay=null,
        IEnumerable<string>? verifiedFloors=null,IEnumerable<string>? renderProfileVerifiedFloors=null,IEnumerable<string>? canonicalGeometryVerifiedFloors=null,
        IEnumerable<string>? sourceStructureVerifiedFloors=null,IEnumerable<string>? provisionalFloors=null,IEnumerable<string>? floors=null)
    {
        MapId=mapId;RouteProfileId=routeProfileId;AnchorFloor=anchorFloor;root=rootPose??throw new ArgumentException("round layer group requires a finite 3x3 root pose");
        Floors=Array.AsReadOnly((floors??RoundPose.DefaultFloors).ToArray());if(!Floors.Contains(anchorFloor))throw new ArgumentException("unsupported anchor floor: "+anchorFloor);
        this.relations[anchorFloor]=Matrix3.Identity;
        foreach(var p in relations??new Dictionary<string,Matrix3>())if(Floors.Contains(p.Key)&&p.Value is not null)this.relations[p.Key]=p.Value;
        if(relationMetadata is not null)foreach(var p in relationMetadata)this.relationMetadata[p.Key]=AlignmentData.CopyMap(p.Value);
        Generation=generation;CycleId=cycleId;Source=source;PoseOrigin=poseOrigin;
        PoseClientSize=poseClientSize is {Length:2}&&poseClientSize.Min()>0?((int[])poseClientSize.Clone()):null;
        displaySignature=(object?[])AlignmentData.Copy((poseDisplaySignature??[]).ToArray())!;
        TrackingFloor=trackingFloor;this.tracking=AlignmentData.CopyMap(tracking);
        if(trackingByFloor is not null)foreach(var p in trackingByFloor)this.trackingByFloor[p.Key]=AlignmentData.CopyMap(p.Value);
        DisplayedFloor=displayedFloor;overlay=displayedOverlay is null?null:(Array)AlignmentData.Copy(displayedOverlay)!;
        verified.UnionWith(verifiedFloors??[]);verified.Add(anchorFloor);renderVerified.UnionWith(renderProfileVerifiedFloors??[]);
        canonicalVerified.UnionWith(canonicalGeometryVerifiedFloors??[]);sourceVerified.UnionWith(sourceStructureVerifiedFloors??[]);provisional.UnionWith(provisionalFloors??[]);
    }
    public string MapId{get;} public string RouteProfileId{get;private set;} public string AnchorFloor{get;} public IReadOnlyList<string> Floors{get;}
    public Matrix3 RootPose{get{lock(sync)return root;}} public long Generation{get;private set;} public AlignmentCycleId? CycleId{get;private set;}
    public string Source{get;private set;} public string PoseOrigin{get;} private int[]? clientSize;
    public int[]? PoseClientSize{get=>clientSize is null?null:(int[])clientSize.Clone();private set=>clientSize=value is null?null:(int[])value.Clone();}
    public IReadOnlyList<object?> PoseDisplaySignature{get{lock(sync)return Array.AsReadOnly((object?[])AlignmentData.Copy(displaySignature)!);}}
    public string? TrackingFloor{get;private set;} public string? DisplayedFloor{get;private set;}
    public Array? DisplayedOverlay{get{lock(sync)return overlay is null?null:(Array)AlignmentData.Copy(overlay)!;}}
    public IReadOnlyDictionary<string,Matrix3> Relations{get{lock(sync)return new ReadOnlyDictionary<string,Matrix3>(new Dictionary<string,Matrix3>(relations));}}
    public IReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>> RelationMetadata{get{lock(sync)return ReadMaps(relationMetadata);}}
    public IReadOnlyDictionary<string,object?> Tracking{get{lock(sync)return AlignmentData.ReadOnly(tracking);}}
    public IReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>> TrackingByFloor{get{lock(sync)return ReadMaps(trackingByFloor);}}
    private static IReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>> ReadMaps(Dictionary<string,Dictionary<string,object?>> maps)=>
        new ReadOnlyDictionary<string,IReadOnlyDictionary<string,object?>>(maps.ToDictionary(p=>p.Key,p=>AlignmentData.ReadOnly(p.Value)));
    public IReadOnlyList<string> VerifiedFloors{get{lock(sync)return Array.AsReadOnly(verified.Order(StringComparer.Ordinal).ToArray());}}
    public IReadOnlyList<string> RenderProfileVerifiedFloors{get{lock(sync)return Array.AsReadOnly(renderVerified.Order(StringComparer.Ordinal).ToArray());}}
    public IReadOnlyList<string> CanonicalGeometryVerifiedFloors{get{lock(sync)return Array.AsReadOnly(canonicalVerified.Order(StringComparer.Ordinal).ToArray());}}
    public IReadOnlyList<string> SourceStructureVerifiedFloors{get{lock(sync)return Array.AsReadOnly(sourceVerified.Order(StringComparer.Ordinal).ToArray());}}
    public IReadOnlyList<string> ProvisionalFloors{get{lock(sync)return Array.AsReadOnly(provisional.Order(StringComparer.Ordinal).ToArray());}}
    /// <summary>Detached candidate; committed group instances are never mutated by a worker.</summary>
    public RoundLayerGroup Copy(string? routeProfileId=null)
    {
        lock(sync)return new(MapId,routeProfileId??RouteProfileId,AnchorFloor,root,
            new Dictionary<string,Matrix3>(relations,StringComparer.Ordinal),
            relationMetadata.ToDictionary(p=>p.Key,p=>(IReadOnlyDictionary<string,object?>)p.Value,StringComparer.Ordinal),
            Generation,CycleId,Source,PoseOrigin,PoseClientSize,displaySignature,TrackingFloor,tracking,
            trackingByFloor.ToDictionary(p=>p.Key,p=>(IReadOnlyDictionary<string,object?>)p.Value,StringComparer.Ordinal),
            DisplayedFloor,overlay,verified,renderVerified,canonicalVerified,sourceVerified,provisional,Floors);
    }
    /// <summary>Records checked geometry without claiming pixels were displayed.</summary>
    public void MarkGeometryVerified(string floor,bool canonicalGeometryVerified,bool sourceStructureVerified)
    {
        lock(sync)
        {
            if(!Floors.Contains(floor))throw new ArgumentException("unsupported floor",nameof(floor));
            verified.Add(floor);provisional.Remove(floor);renderVerified.Remove(floor);
            if(canonicalGeometryVerified)canonicalVerified.Add(floor);else canonicalVerified.Remove(floor);
            if(sourceStructureVerified)sourceVerified.Add(floor);else sourceVerified.Remove(floor);
        }
    }
    /// <summary>Display-owned replacement. Root, geometry proofs and group identity stay unchanged.</summary>
    public void RebaseDisplayProfile(string profile,string floor,Array pixels)
    {
        lock(sync)
        {
            if(!Floors.Contains(floor))throw new ArgumentException("unsupported floor",nameof(floor));
            RouteProfileId=profile;DisplayedFloor=floor;overlay=(Array)AlignmentData.Copy(pixels)!;
            renderVerified.Clear();
        }
    }
    public bool SetRelation(string floor,Matrix3 relation,IReadOnlyDictionary<string,object?>? metadata=null)
    {lock(sync){if(!Floors.Contains(floor)||relation is null)return false;var owned=metadata is null?null:AlignmentData.CopyMap(metadata);
        relations[floor]=relation;if(owned is not null)relationMetadata[floor]=owned;return true;}}
    public Matrix3? CanonicalPoseForFloor(string floor){lock(sync)return relations.TryGetValue(floor,out var r)?Matrix3.Multiply(root,r):null;}
    public Matrix3? ProfilePoseForFloor(IRoundCoordinateAdapters? adapters,string floor)
    {lock(sync){var canonical=CanonicalPoseForFloor(floor);return canonical is null?null:RouteProfileId=="source-structure"?canonical:
        adapters?.ComposeProfileToClientFromCanonical(RouteProfileId,MapId,floor,canonical);}}
    public Matrix3? RecognitionPoseForFloor(IRoundCoordinateAdapters? adapters,string floor)
    {lock(sync){var canonical=CanonicalPoseForFloor(floor);return canonical is null?null:RouteProfileId=="source-structure"?canonical:
        adapters?.RecognitionFromCanonicalToClient(MapId,floor,canonical);}}
    public bool ApplyTranslation(double dx,double dy,string source,AlignmentCycleId? cycleId)
    {lock(sync){if(!double.IsFinite(dx)||!double.IsFinite(dy)||Math.Sqrt(dx*dx+dy*dy)<1e-6)return false;
        root=root.WithTranslation(dx,dy);Generation++;Source=source;CycleId=cycleId;overlay=null;return true;}}
    public static Matrix3? CenteredClientReprojection(IReadOnlyList<int>? sourceSize,IReadOnlyList<int>? targetSize)
    {
        if(sourceSize is not {Count:2}||targetSize is not {Count:2}||sourceSize.Any(v=>v<=0)||targetSize.Any(v=>v<=0))return null;
        double scale=Math.Min((double)targetSize[0]/sourceSize[0],(double)targetSize[1]/sourceSize[1]);
        return new(new[]{new[]{scale,0,(targetSize[0]-sourceSize[0]*scale)/2},new[]{0d,scale,(targetSize[1]-sourceSize[1]*scale)/2},new[]{0d,0,1}});
    }
    public Dictionary<string,object?> ReprojectClientPose(int[]? targetSize,string source,AlignmentCycleId? cycleId)
    {lock(sync)
    {
        if(targetSize is not {Length:2}||targetSize.Any(v=>v<=0))return Failure("target-client-size-invalid");
        int[] target=(int[])targetSize.Clone();var original=PoseClientSize;var reprojection=CenteredClientReprojection(original,target);
        if(reprojection is null){PoseClientSize=target;overlay=null;return new(){{"success",false},{"reason","source-client-size-unavailable"},{"targetSize",target}};}
        root=Matrix3.Multiply(reprojection,root)??throw new ArithmeticException("client reprojection became nonfinite");
        Generation++;Source=source;CycleId=cycleId;PoseClientSize=target;overlay=null;
        foreach(var stored in trackingByFloor.Values)
        {
            foreach(string key in new[]{"translationMask","translationMaskMatrix","layoutAnchorMask","layoutAnchorMatrix","bounds","renderedProfileResidual"})stored[key]=null;
            stored["translationAnchorHistory"]=Array.Empty<object>();
            foreach(string key in new[]{"poseVerifiedAgainstRenderedProfile","poseVerifiedAgainstCanonicalGeometry","poseVerifiedAgainstLiveGeometry"})stored[key]=false;
        }
        tracking=AlignmentData.CopyMap(tracking);
        return new(){{"success",true},{"sourceSize",original},{"targetSize",target},{"scale",reprojection[0,0]},
            {"offset",new[]{reprojection[0,2],reprojection[1,2]}},{"generation",Generation}};
    }}
    public Dictionary<string,object?> AdoptFloorCanonicalPose(string floor,Matrix3 floorPose,string source,AlignmentCycleId? cycleId,double maximumLinearDeltaRatio=.65)
    {lock(sync)
    {
        if(!relations.TryGetValue(floor,out var relation)||floorPose is null)return Failure("floor-relation-unavailable");
        if(relation.Inverse() is not { } inverse)return Failure("floor-relation-singular");
        var candidate=Matrix3.Multiply(floorPose,inverse)??throw new ArithmeticException("floor root became nonfinite");
        double ratio=Matrix3.LinearNorm(candidate,root)/Math.Max(Matrix3.LinearNorm(root),1e-6);
        if(ratio>maximumLinearDeltaRatio)return new(){{"success",false},{"reason","shared-linear-transform-disagrees"},{"linearDeltaRatio",ratio}};
        double dx=candidate[0,2]-root[0,2],dy=candidate[1,2]-root[1,2];
        bool changed=Math.Sqrt(dx*dx+dy*dy)>=.25||ratio>=1e-5;
        if(changed){root=candidate;Generation++;Source=source;CycleId=cycleId;overlay=null;}
        verified.Add(floor);provisional.Remove(floor);
        return new(){{"success",true},{"changed",changed},{"dx",dx},{"dy",dy},{"linearDeltaRatio",ratio},{"generation",Generation}};
    }}
    public void SetTracking(string floor,IReadOnlyDictionary<string,object?> evidence,AlignmentCycleId? cycleId)
    {lock(sync){var owned=AlignmentData.CopyMap(evidence);TrackingFloor=floor;tracking=owned;trackingByFloor[floor]=tracking;CycleId=cycleId;}}
    public IReadOnlyDictionary<string,object?> TrackingForFloor(string floor)
    {lock(sync)return AlignmentData.ReadOnly(trackingByFloor.TryGetValue(floor,out var stored)?stored:TrackingFloor==floor?tracking:new Dictionary<string,object?>());}
    public void MarkDisplayed(string floor,Array displayedOverlay,bool provisional,bool renderProfileVerified=false,bool canonicalGeometryVerified=false,bool sourceStructureVerified=false)
    {lock(sync)
    {
        var owned=(Array)AlignmentData.Copy(displayedOverlay)!;DisplayedFloor=floor;overlay=owned;
        if(provisional)this.provisional.Add(floor);else{verified.Add(floor);this.provisional.Remove(floor);}
        Mark(renderVerified,renderProfileVerified);Mark(canonicalVerified,canonicalGeometryVerified);Mark(sourceVerified,sourceStructureVerified);
        void Mark(HashSet<string> set,bool enabled){if(enabled)set.Add(floor);else set.Remove(floor);}
    }}
    public Dictionary<string,object?> Snapshot()
    {lock(sync)return new(){{"mapId",MapId},{"routeProfileId",RouteProfileId},{"anchorFloor",AnchorFloor},{"rootPose",root.ToArray()},
        {"availableFloors",relations.Keys.Order(StringComparer.Ordinal).ToArray()},{"generation",Generation},{"cycleId",CycleId?.Value},{"source",Source},
        {"poseOrigin",PoseOrigin},{"poseClientSize",PoseClientSize},{"poseDisplaySignature",AlignmentData.Copy(displaySignature)},
        {"trackingFloor",TrackingFloor},{"trackingEvidenceFloors",trackingByFloor.Keys.Order(StringComparer.Ordinal).ToArray()},
        {"displayedFloor",DisplayedFloor},{"verifiedFloors",VerifiedFloors},{"renderProfileVerifiedFloors",RenderProfileVerifiedFloors},
        {"canonicalGeometryVerifiedFloors",CanonicalGeometryVerifiedFloors},{"sourceStructureVerifiedFloors",SourceStructureVerifiedFloors},
        {"provisionalFloors",ProvisionalFloors},{"singlePoseOwner",true}};}
    private static Dictionary<string,object?> Failure(string reason)=>new(){{"success",false},{"reason",reason}};
}
