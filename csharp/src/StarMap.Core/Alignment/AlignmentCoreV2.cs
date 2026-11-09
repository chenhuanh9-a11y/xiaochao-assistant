using System.Collections.ObjectModel;
using System.Diagnostics;

namespace StarMap.Core.Alignment;

/// <summary>Original pure V2 lifecycle. Identity, reviewed resource relations and sensor verification live outside this owner.</summary>
public sealed class AlignmentCoreV2
{
    public const double HighConfidence=.80;
    public const int MinDynamicAnchors=3,EventHistoryLimit=256;
    private readonly object sync=new();
    private readonly Func<double> clock;
    private readonly Action<string,IReadOnlyDictionary<string,object?>>? eventSink;
    private readonly Dictionary<string,RoundPose> poses=new(StringComparer.Ordinal);
    private readonly Dictionary<long,RoundPose> authorizedPoses=[];
    private readonly Dictionary<(string Map,string Floor),Dictionary<string,object?>> anchors=[];
    private readonly Dictionary<(string Map,string Floor),List<AlignmentCandidate>> cache=[];
    private readonly Dictionary<long,List<AlignmentCandidate>> pending=[];
    private readonly List<Dictionary<string,object?>> events=[];
    private long nextObservationId,nextAuthorizationId;
    private Observation? observation;
    private DisplayAuthorization? authorization;
    public AlignmentCoreV2(Func<double>? clock=null,Action<string,IReadOnlyDictionary<string,object?>>? eventSink=null,IEnumerable<string>? floors=null)
    {this.clock=clock??(()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency);this.eventSink=eventSink;Floors=Array.AsReadOnly((floors??RoundPose.DefaultFloors).ToArray());}
    public IReadOnlyList<string> Floors{get;}
    public double ContinuityWindowSeconds{get;set;}=2.5;
    public Observation? ActiveObservation{get{lock(sync)return observation;}}
    public RoundPose? ActivePose{get{lock(sync)return observation is null?null:poses.GetValueOrDefault(observation.MapId);}}
    public DisplayAuthorization? DisplayAuthorization{get{lock(sync)return authorization;}}
    public IReadOnlyDictionary<string,RoundPose> PoseByMap{get{lock(sync)return new ReadOnlyDictionary<string,RoundPose>(new Dictionary<string,RoundPose>(poses));}}
    public IReadOnlyDictionary<(string Map,string Floor),IReadOnlyDictionary<string,object?>> AnchorPools
    {get{lock(sync)return new ReadOnlyDictionary<(string Map,string Floor),IReadOnlyDictionary<string,object?>>(anchors.ToDictionary(p=>p.Key,p=>AlignmentData.ReadOnly(p.Value)));}}
    public IReadOnlyDictionary<(string Map,string Floor),IReadOnlyList<AlignmentCandidate>> CandidateCache
    {get{lock(sync)return new ReadOnlyDictionary<(string Map,string Floor),IReadOnlyList<AlignmentCandidate>>(cache.ToDictionary(p=>p.Key,p=>(IReadOnlyList<AlignmentCandidate>)Array.AsReadOnly(p.Value.ToArray())));}}
    public IReadOnlyDictionary<long,IReadOnlyList<AlignmentCandidate>> PendingCandidates
    {get{lock(sync)return new ReadOnlyDictionary<long,IReadOnlyList<AlignmentCandidate>>(pending.ToDictionary(p=>p.Key,p=>(IReadOnlyList<AlignmentCandidate>)Array.AsReadOnly(p.Value.ToArray())));}}
    public IReadOnlyList<IReadOnlyDictionary<string,object?>> Events{get{lock(sync)return Array.AsReadOnly(events.Select(AlignmentData.ReadOnly).ToArray());}}
    private void Emit(string name,params (string Key,object? Value)[] values)
    {
        var payload=new Dictionary<string,object?>{{"event",name}};
        foreach(var p in values)payload[p.Key]=AlignmentData.Copy(p.Value);
        events.Add(payload);if(events.Count>EventHistoryLimit)events.RemoveRange(0,events.Count-EventHistoryLimit);
        eventSink?.Invoke(name,AlignmentData.ReadOnly(payload));
    }
    public Observation BeginObservation(AlignmentCycleId cycleId,string mapId,string floor,long frameGeneration,
        IEnumerable<object?>? frameSignature=null,string reason="open",double? now=null)
    {lock(sync)
    {
        if(!Floors.Contains(floor))throw new ArgumentException("unsupported floor: "+floor);
        var previous=observation;var token=authorization;
        var signature=(object?[])AlignmentData.Copy((frameSignature??[]).ToArray())!;
        bool continuity=previous is not null&&!previous.Closed&&previous.CycleId==cycleId&&previous.MapId==mapId&&previous.Floor==floor&&token is {Active:true};
        if(previous is not null)previous.Closed=true;
        double time=now??clock();
        if(continuity&&token!.ContinuityUntil>0&&token.ContinuityUntil<=time){RevokeDisplay("continuity-expired");continuity=false;}
        if(continuity)
        {
            if(token!.ContinuityUntil<=time)token.ContinuityUntil=time+ContinuityWindowSeconds;
            Emit("alignment-v2-display-continuity-started",("authorizationId",token.AuthorizationId),("cycleId",cycleId.Value),
                ("mapId",mapId),("floor",floor),("fromFrameGeneration",token.FrameGeneration),("until",token.ContinuityUntil));
        }
        else RevokeDisplay("new-observation");
        var current=new Observation(++nextObservationId,cycleId,mapId,floor,frameGeneration,time,signature);
        pending.Clear();authorizedPoses.Clear();observation=current;pending[current.ObservationId]=[];
        Emit("alignment-v2-observation-opened",("observationId",current.ObservationId),("cycleId",cycleId.Value),
            ("mapId",mapId),("floor",floor),("frameGeneration",frameGeneration),("reason",reason));
        return current;
    }}
    public bool UpdateFrame(long frameGeneration,string? floor=null,IEnumerable<object?>? frameSignature=null)
    {lock(sync)
    {
        var current=observation;if(current is null||current.Closed)return false;
        var signature=frameSignature?.ToArray();
        var ownedSignature=signature is {Length:>0}?(object?[])AlignmentData.Copy(signature)!:null;
        current.FrameGeneration=frameGeneration;
        if(floor is not null){if(!Floors.Contains(floor))return false;current.Floor=floor;}
        if(ownedSignature is not null)current.Signature=ownedSignature;
        current.TerminalVerdict=null;current.TerminalReason=null;RevokeDisplay("new-frame");pending[current.ObservationId]=[];return true;
    }}
    public bool RecordStage(string name,double elapsedMs)
    {lock(sync){if(observation is null||observation.TerminalVerdict is not null)return false;observation.Stage(name,elapsedMs);return true;}}
    public bool SetInitialPose(RoundPose pose,string reason="initial")
    {lock(sync){if(!pose.Reliable)return false;poses[pose.MapId]=pose;Emit("alignment-v2-pose-seeded",("mapId",pose.MapId),
        ("generation",pose.Generation),("source",reason),("singlePoseOwner",true));return true;}}
    public IReadOnlyDictionary<string,object?> UpdateAnchorPool(string mapId,string floor,IReadOnlyDictionary<string,object?>? evidence,long frameGeneration,bool success)
    {lock(sync)
    {
        var key=(mapId,floor);var previous=anchors.GetValueOrDefault(key);
        if(success){var parsed=AlignmentData.CopyMap(evidence);parsed["mapId"]=mapId;parsed["floor"]=floor;parsed["frameGeneration"]=frameGeneration;anchors[key]=parsed;}
        else if(previous is null)anchors[key]=new(){{"mapId",mapId},{"floor",floor},{"frameGeneration",frameGeneration},{"anchors",Array.Empty<object>()}};
        else Emit("alignment-v2-anchor-pool-retained",("mapId",mapId),("floor",floor),("frameGeneration",frameGeneration),
            ("previousFrameGeneration",AlignmentData.Get(previous,"frameGeneration")));
        return AlignmentData.ReadOnly(anchors[key]);
    }}
    public bool CacheCandidate(AlignmentCandidate candidate,string reason="late-background-result")
    {lock(sync)
    {
        var key=(candidate.MapId,candidate.Floor);if(!cache.TryGetValue(key,out var rows))cache[key]=rows=[];
        rows.Add(candidate);if(rows.Count>8)rows.RemoveRange(0,rows.Count-8);
        Emit("alignment-v2-candidate-cached",("mapId",key.MapId),("floor",key.Floor),("frameGeneration",candidate.FrameGeneration),
            ("source",candidate.Source),("reason",reason),("displayAuthorized",false));return true;
    }}
    private Adjudication Reject(string reason,AlignmentCandidate? candidate=null,bool terminal=false)
    {
        var current=observation;var id=current?.ObservationId;
        if(terminal&&current is not null&&current.TerminalVerdict is null)
        {current.TerminalVerdict="rejected";current.TerminalReason=reason;Emit("alignment-v2-terminal-verdict",("observationId",current.ObservationId),("verdict","rejected"),("reason",reason));}
        return new(false,reason,id,candidate,Terminal:terminal);
    }
    public Adjudication Adjudicate(AlignmentCandidate candidate)
    {lock(sync)
    {
        // The synchronous sink may replace active state. This call still belongs to its entry observation, as in Python.
        var current=observation;
        if(current is null||current.Closed)return new(false,"observation-not-active",null,candidate);
        if(current.TerminalVerdict is not null)return new(false,"observation-already-terminal",current.ObservationId,candidate);
        if(candidate.MapId!=current.MapId)return Reject("candidate-map-stale",candidate);
        if(candidate.Floor!=current.Floor)return Reject("candidate-floor-stale",candidate);
        if(candidate.FrameGeneration!=current.FrameGeneration)return Reject("candidate-frame-stale",candidate);
        if(candidate.IdentityRerank)return Reject("identity-rerank-not-alignment",candidate);
        if(!candidate.CurrentFrameVerified)return Reject("current-frame-verification-required",candidate);
        bool high=(candidate.AnchorKind is "door" or "dynamic-anchor")&&candidate.Confidence>=HighConfidence
            &&candidate.AnchorCount>=(candidate.AnchorKind=="door"?1:MinDynamicAnchors);
        if(candidate.CrossFloor&&!(candidate.DeterministicRelation&&candidate.TargetLocalCorrectionVerified))return Reject("cross-floor-local-correction-required",candidate);
        if(!high&&!candidate.ResidualVerified)return Reject("current-frame-residual-unverified",candidate);
        if(!candidate.CrossFloor&&candidate.Source.Contains("same-floor-reopen",StringComparison.Ordinal))
        {
            if(!candidate.TranslationOnly)return Reject("same-floor-reopen-must-be-translation-only",candidate);
            var old=poses.GetValueOrDefault(candidate.MapId)?.PoseForFloor(candidate.Floor);
            if(poses.ContainsKey(candidate.MapId)&&(old is null||!old.LinearAllClose(candidate.MatrixSourceToClient)))return Reject("same-floor-reopen-linear-change",candidate);
        }
        var previous=poses.GetValueOrDefault(candidate.MapId);
        bool independent=previous is not null&&previous.PoseForFloor(candidate.Floor) is null
            &&AlignmentData.True(candidate.OwnedMetadata,"independentFloorAnchor")&&candidate.Source=="initial-structure-v2"&&candidate.AnchorKind=="global"
            &&candidate.GlobalResidualVerified&&candidate.ResidualVerified&&!candidate.CrossFloor;
        RoundPose? pose;
        if(previous is null||independent)
            pose=new(candidate.MapId,AlignmentData.Truth(AlignmentData.Get(candidate.OwnedMetadata,"routeProfileId"))?
                AlignmentData.String(AlignmentData.Get(candidate.OwnedMetadata,"routeProfileId")):"default",candidate.Floor,candidate.MatrixSourceToClient,
                generation:independent?previous!.Generation+1:0,reliable:true,source:candidate.Source,floors:Floors);
        else {pose=previous.AdoptFloorPose(candidate.Floor,candidate.MatrixSourceToClient,candidate.Source);if(pose is null)return Reject("floor-relation-unavailable",candidate);}
        current.TerminalVerdict="accepted";current.TerminalReason=candidate.Source;pending[current.ObservationId].Add(candidate);
        Emit("alignment-v2-terminal-verdict",("observationId",current.ObservationId),("verdict","accepted"),("reason",candidate.Source),
            ("anchorKind",candidate.AnchorKind),("globalResidualIgnored",high&&!candidate.GlobalResidualVerified));
        return new(true,"accepted",current.ObservationId,candidate,pose);
    }}
    public DisplayAuthorization? Authorize(Adjudication adjudication,string? source=null,double? now=null)
    {lock(sync)
    {
        var current=observation;
        if(!adjudication.Accepted||current is null||adjudication.ObservationId!=current.ObservationId||adjudication.Pose is null||current.Closed)return null;
        RevokeDisplay("new-authorization");
        string origin=!string.IsNullOrEmpty(source)?source:adjudication.Candidate?.Source??throw new InvalidOperationException("Accepted adjudication has no candidate source");
        var token=new DisplayAuthorization(++nextAuthorizationId,current.ObservationId,current.CycleId,current.MapId,current.Floor,
            current.FrameGeneration,adjudication.Pose.Generation,origin,now??clock());
        authorization=token;authorizedPoses[token.AuthorizationId]=adjudication.Pose;
        Emit("alignment-v2-display-authorized",("authorizationId",token.AuthorizationId),("observationId",token.ObservationId),
            ("cycleId",token.CycleId.Value),("mapId",token.MapId),("floor",token.Floor),("frameGeneration",token.FrameGeneration),
            ("poseGeneration",token.PoseGeneration),("source",token.Source));return token;
    }}
    public bool Commit(DisplayAuthorization token,bool persistPose=true)
    {lock(sync)
    {
        var current=observation;
        if(current is null||!token.Current(current.ObservationId,current.CycleId,current.MapId,current.Floor,current.FrameGeneration))return false;
        if(!authorizedPoses.Remove(token.AuthorizationId,out var pose))return false;
        if(current.TerminalVerdict is not null)pending.Remove(token.ObservationId);
        if(persistPose)poses[pose.MapId]=pose;
        Emit("alignment-v2-commit",("authorizationId",token.AuthorizationId),("observationId",current.ObservationId),("mapId",pose.MapId),
            ("floor",pose.AnchorFloor),("poseGeneration",pose.Generation),("singlePoseOwner",true),("persistentPoseUpdated",persistPose));return true;
    }}
    public DisplayAuthorization? AuthorizeSharedProjection()
    {lock(sync)
    {
        var current=observation;var pose=current is null?null:poses.GetValueOrDefault(current.MapId);
        if(current is null||current.Closed||pose is null||!pose.Reliable||current.Floor is not ("1" or "2")||pose.PoseForFloor(current.Floor) is null)return null;
        var token=Authorize(new(true,"shared-root-provisional",current.ObservationId,Pose:pose,Terminal:false),"shared-root-provisional");
        return token is not null&&Commit(token,false)?token:null;
    }}
    public DisplayAuthorization? AuthorizeProvisionalTarget()
    {lock(sync)
    {
        var current=observation;var pose=current is null?null:poses.GetValueOrDefault(current.MapId);
        if(current is null||current.Closed||pose is null||!pose.Reliable||current.Floor is not ("1" or "2"))return null;
        var token=Authorize(new(true,"source-marker-provisional",current.ObservationId,Pose:pose,Terminal:false),"source-marker-provisional");
        return token is not null&&Commit(token,false)?token:null;
    }}
    public DisplayAuthorization? RecordVerifiedDisplay(string mapId,string floor,Matrix3 matrixSourceToClient,string source,long frameGeneration,bool poseVerified=true)
    {lock(sync)
    {
        var current=observation;
        if(current is null||!current.Current(current.CycleId,frameGeneration)||current.MapId!=mapId||current.Floor!=floor)return null;
        var candidate=new AlignmentCandidate(mapId,floor,matrixSourceToClient,source,frameGeneration,poseVerified?1:0,1,"verified-display",
            currentFrameVerified:poseVerified,residualVerified:poseVerified,metadata:new Dictionary<string,object?>{{"routeProfileId",poses.GetValueOrDefault(mapId)?.RouteProfileId??"default"}});
        var token=Authorize(Adjudicate(candidate),source);
        if(token is null||!Commit(token)){if(token is not null)RevokeDisplay("verified-display-commit-failed");return null;}return token;
    }}
    public bool PreserveEquivalentDisplay()
    {lock(sync)
    {
        var current=observation;var token=authorization;
        if(current is null||current.Closed||token is null||!CanDisplay(current.CycleId,current.MapId,current.Floor,current.FrameGeneration))return false;
        token.ObservationId=current.ObservationId;token.FrameGeneration=current.FrameGeneration;token.ContinuityUntil=0;
        current.TerminalVerdict="accepted";current.TerminalReason="equivalent-verified-pixels";return true;
    }}
    public bool CanDisplay(AlignmentCycleId cycleId,string mapId,string floor,long frameGeneration,DisplayAuthorization? token=null)
    {lock(sync){var current=observation;token??=authorization;if(current is not null&&token is not null&&token.Current(current.ObservationId,cycleId,mapId,floor,frameGeneration))return true;
        return ContinuityCurrent(cycleId,mapId,floor,frameGeneration,token);}}
    public bool ContinuityCurrent(AlignmentCycleId cycleId,string mapId,string floor,long frameGeneration,DisplayAuthorization? token=null)
    {lock(sync)
    {
        var current=observation;token??=authorization;if(current is null||token is null||!token.Active)return false;
        if(clock()>token.ContinuityUntil){RevokeDisplay("continuity-expired");return false;}
        return token.ContinuityUntil>0&&token.ObservationId!=current.ObservationId&&token.CycleId==cycleId&&token.MapId==mapId&&token.Floor==floor
            &&current.CycleId==cycleId&&current.MapId==mapId&&current.Floor==floor&&frameGeneration>=token.FrameGeneration;
    }}
    public void RevokeDisplay(string reason="hidden")
    {lock(sync)
    {
        var token=authorization;if(token is null)return;token.Active=false;authorizedPoses.Remove(token.AuthorizationId);
        var current=observation;
        if(current is null||current.ObservationId!=token.ObservationId||current.Closed||current.TerminalVerdict is not null)
            pending.Remove(token.ObservationId);
        Emit("alignment-v2-display-revoked",("authorizationId",token.AuthorizationId),("observationId",token.ObservationId),("reason",reason));
    }}
    public bool Close(AlignmentCycleId? cycleId=null,string reason="map-closed")
    {lock(sync)
    {
        var current=observation;
        if(current is null){RevokeDisplay(reason);return false;}
        if(cycleId is not null&&current.CycleId!=cycleId.Value)return false;
        current.Closed=true;pending.Remove(current.ObservationId);
        if(current.TerminalVerdict is null){current.TerminalVerdict="closed";current.TerminalReason=reason;Emit("alignment-v2-terminal-verdict",("observationId",current.ObservationId),("verdict","closed"),("reason",reason));}
        RevokeDisplay(reason);Emit("alignment-v2-observation-closed",("observationId",current.ObservationId),("cycleId",current.CycleId.Value),
            ("mapId",current.MapId),("floor",current.Floor),("posePreserved",poses.ContainsKey(current.MapId)),
            ("anchorPoolPreserved",anchors.ContainsKey((current.MapId,current.Floor))),("reason",reason));return true;
    }}
    public void ResetRound(string reason="round-reset")
    {lock(sync){RevokeDisplay(reason);observation=null;poses.Clear();authorizedPoses.Clear();anchors.Clear();cache.Clear();pending.Clear();
        Emit("alignment-v2-round-reset",("reason",reason),("singlePoseOwner",true));}}
    public Dictionary<string,object?> Snapshot()
    {lock(sync)return new(){{"activeObservation",observation is null?null:new Dictionary<string,object?>{{"observationId",observation.ObservationId},
        {"cycleId",observation.CycleId.Value},{"mapId",observation.MapId},{"floor",observation.Floor},{"frameGeneration",observation.FrameGeneration},
        {"terminalVerdict",observation.TerminalVerdict},{"closed",observation.Closed},{"stages",observation.Stages.ToDictionary(p=>p.Key,p=>(object?)p.Value)}}},
        {"poses",poses.OrderBy(p=>p.Key,StringComparer.Ordinal).ToDictionary(p=>p.Key,p=>(object?)p.Value.Snapshot())},
        {"displayAuthorized",authorization is {Active:true}},{"anchorPoolKeys",anchors.Keys.OrderBy(k=>k.Map,StringComparer.Ordinal).ThenBy(k=>k.Floor,StringComparer.Ordinal).Select(k=>new[]{k.Map,k.Floor}).ToArray()},
        {"singlePoseOwner",true}};}
}
