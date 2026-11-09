using System.Collections.ObjectModel;

namespace StarMap.Core.Alignment;

public sealed class RoundPose
{
    public static readonly IReadOnlyList<string> DefaultFloors=Array.AsReadOnly(new[]{"B1","1","2"});
    private readonly Dictionary<string,Matrix3> relations;
    public RoundPose(string mapId,string routeProfileId,string anchorFloor,Matrix3 rootMatrix,
        IReadOnlyDictionary<string,Matrix3>? floorRelations=null,long generation=0,bool reliable=false,string source="unknown",IEnumerable<string>? floors=null)
    {
        MapId=mapId;RouteProfileId=routeProfileId;AnchorFloor=anchorFloor;RootMatrix=rootMatrix??throw new ArgumentException("RoundPose requires a finite 3x3 matrix");
        Floors=Array.AsReadOnly((floors??DefaultFloors).ToArray());
        if(!Floors.Contains(anchorFloor))throw new ArgumentException("unsupported anchor floor: "+anchorFloor);
        relations=new(StringComparer.Ordinal){{anchorFloor,Matrix3.Identity}};
        foreach(var p in floorRelations??new Dictionary<string,Matrix3>())if(Floors.Contains(p.Key)&&p.Value is not null)relations[p.Key]=p.Value;
        Generation=generation;Reliable=reliable;Source=source;
    }
    public string MapId{get;} public string RouteProfileId{get;} public string AnchorFloor{get;} public Matrix3 RootMatrix{get;}
    public long Generation{get;} public bool Reliable{get;} public string Source{get;} public IReadOnlyList<string> Floors{get;}
    public IReadOnlyDictionary<string,Matrix3> FloorRelations=>new ReadOnlyDictionary<string,Matrix3>(new Dictionary<string,Matrix3>(relations,StringComparer.Ordinal));
    public RoundPose WithFloorRelation(string floor,Matrix3 relation)
    {var next=new Dictionary<string,Matrix3>(relations,StringComparer.Ordinal);if(Floors.Contains(floor))next[floor]=relation;
        return new(MapId,RouteProfileId,AnchorFloor,RootMatrix,next,Generation,Reliable,Source,Floors);}
    public Matrix3? PoseForFloor(string floor)=>relations.TryGetValue(floor,out var r)?Matrix3.Multiply(RootMatrix,r):null;
    public RoundPose WithTranslation(double dx,double dy,string source)=>new(MapId,RouteProfileId,AnchorFloor,RootMatrix.WithTranslation(dx,dy),relations,Generation+1,Reliable,source,Floors);
    public RoundPose? AdoptFloorPose(string floor,Matrix3 floorMatrix,string source,bool reliable=true)
    {if(!relations.TryGetValue(floor,out var r)||r.Inverse() is not { } inverse||Matrix3.Multiply(floorMatrix,inverse) is not { } root)return null;
        return new(MapId,RouteProfileId,AnchorFloor,root,relations,Generation+1,reliable,source,Floors);}
    public Dictionary<string,object?> Snapshot()=>new(){{"mapId",MapId},{"routeProfileId",RouteProfileId},{"anchorFloor",AnchorFloor},
        {"rootMatrix",RootMatrix.ToArray()},{"floorRelations",relations.OrderBy(p=>p.Key,StringComparer.Ordinal).ToDictionary(p=>p.Key,p=>(object?)p.Value.ToArray())},
        {"generation",Generation},{"reliable",Reliable},{"source",Source},{"singlePoseOwner",true}};
}

public sealed class Observation
{
    private readonly Dictionary<string,double> stages=new(StringComparer.Ordinal);
    internal Observation(long id,AlignmentCycleId cycle,string map,string floor,long frame,double opened,object?[] signature)
    {ObservationId=id;CycleId=cycle;MapId=map;Floor=floor;FrameGeneration=frame;OpenedAt=opened;Signature=signature;}
    public long ObservationId{get;} public AlignmentCycleId CycleId{get;} public string MapId{get;}
    public string Floor{get;internal set;} public long FrameGeneration{get;internal set;} public double OpenedAt{get;}
    internal object?[] Signature{get;set;}
    public IReadOnlyList<object?> FrameSignature=>Array.AsReadOnly((object?[])AlignmentData.Copy(Signature)!);
    public IReadOnlyDictionary<string,double> Stages{get {lock(stages)return new ReadOnlyDictionary<string,double>(new Dictionary<string,double>(stages));}}
    public string? TerminalVerdict{get;internal set;} public string? TerminalReason{get;internal set;} public bool Closed{get;internal set;}
    public bool Current(AlignmentCycleId cycleId,long frameGeneration)=>!Closed&&CycleId==cycleId&&FrameGeneration==frameGeneration;
    public void Stage(string name,double elapsedMs){lock(stages)if(TerminalVerdict is null)stages[name]=elapsedMs>0?elapsedMs:0;}
}

public sealed class AlignmentCandidate
{
    private readonly Dictionary<string,object?> metadata;
    public AlignmentCandidate(string mapId,string floor,Matrix3 matrixSourceToClient,string source,long frameGeneration,
        double confidence=0,int anchorCount=0,string anchorKind="global",bool currentFrameVerified=false,bool residualVerified=false,
        bool globalResidualVerified=false,bool translationOnly=false,bool crossFloor=false,bool deterministicRelation=false,
        bool targetLocalCorrectionVerified=false,bool identityRerank=false,bool explicitDiscontinuity=false,
        IReadOnlyDictionary<string,object?>? metadata=null)
    {MapId=mapId;Floor=floor;MatrixSourceToClient=matrixSourceToClient??throw new ArgumentException("alignment candidate requires a finite 3x3 matrix");
        Source=source;FrameGeneration=frameGeneration;Confidence=confidence;AnchorCount=anchorCount;AnchorKind=anchorKind;
        CurrentFrameVerified=currentFrameVerified;ResidualVerified=residualVerified;GlobalResidualVerified=globalResidualVerified;
        TranslationOnly=translationOnly;CrossFloor=crossFloor;DeterministicRelation=deterministicRelation;TargetLocalCorrectionVerified=targetLocalCorrectionVerified;
        IdentityRerank=identityRerank;ExplicitDiscontinuity=explicitDiscontinuity;this.metadata=AlignmentData.CopyMap(metadata);}
    public string MapId{get;} public string Floor{get;} public Matrix3 MatrixSourceToClient{get;} public string Source{get;} public long FrameGeneration{get;}
    public double Confidence{get;} public int AnchorCount{get;} public string AnchorKind{get;} public bool CurrentFrameVerified{get;}
    public bool ResidualVerified{get;} public bool GlobalResidualVerified{get;} public bool TranslationOnly{get;} public bool CrossFloor{get;}
    public bool DeterministicRelation{get;} public bool TargetLocalCorrectionVerified{get;} public bool IdentityRerank{get;} public bool ExplicitDiscontinuity{get;}
    public IReadOnlyDictionary<string,object?> Metadata=>AlignmentData.ReadOnly(metadata);
    internal IReadOnlyDictionary<string,object?> OwnedMetadata=>metadata;
}

public sealed record Adjudication(bool Accepted,string Reason,long? ObservationId,AlignmentCandidate? Candidate=null,RoundPose? Pose=null,bool Terminal=true);

/// <summary>Read-only handle to a core-owned authorization. Revocation remains visible to holders.</summary>
public sealed class DisplayAuthorization
{
    internal DisplayAuthorization(long id,long observation,AlignmentCycleId cycle,string map,string floor,long frame,long generation,string source,double issued)
    {AuthorizationId=id;ObservationId=observation;CycleId=cycle;MapId=map;Floor=floor;FrameGeneration=frame;PoseGeneration=generation;Source=source;IssuedAt=issued;}
    public long AuthorizationId{get;} public long ObservationId{get;internal set;} public AlignmentCycleId CycleId{get;} public string MapId{get;}
    public string Floor{get;} public long FrameGeneration{get;internal set;} public long PoseGeneration{get;} public string Source{get;} public double IssuedAt{get;}
    public bool Active{get;internal set;}=true; public double ContinuityUntil{get;internal set;}
    public bool Current(long observationId,AlignmentCycleId cycleId,string mapId,string floor,long frameGeneration)=>Active
        &&ObservationId==observationId&&CycleId==cycleId&&MapId==mapId&&Floor==floor&&FrameGeneration==frameGeneration;
    public Dictionary<string,object?> Snapshot()=>new(){{"authorizationId",AuthorizationId},{"observationId",ObservationId},{"cycleId",CycleId.Value},
        {"mapId",MapId},{"floor",Floor},{"frameGeneration",FrameGeneration},{"poseGeneration",PoseGeneration},{"source",Source},
        {"issuedAt",IssuedAt},{"active",Active},{"continuityUntil",ContinuityUntil}};
}

public interface IRoundCoordinateAdapters
{
    Matrix3? ComposeProfileToClientFromCanonical(string profile,string mapId,string floor,Matrix3 canonicalToClient);
    Matrix3? RecognitionFromCanonicalToClient(string mapId,string floor,Matrix3 canonicalToClient);
}
