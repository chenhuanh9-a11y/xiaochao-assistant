using System.Runtime.InteropServices;
using System.Text.Json;
using StarMap.Core.Alignment;

/// <summary>Real synchronous sinks and actual evidence owners; no substituted lifecycle or copy implementation.</summary>
internal static class AlignmentRegressionChecks
{
    public static int Run(string output)
    {
        var results=new List<Dictionary<string,object?>>();
        Check("adjudicate-sink-begins-new-context",()=>
        {
            AlignmentCoreV2 core=null!;bool armed=true;
            core=new(()=>10,(name,payload)=>
            {
                if(armed&&name=="alignment-v2-terminal-verdict"&&Equals(payload["verdict"],"accepted"))
                {armed=false;core.BeginObservation(2,"B","2",7);}
            });
            var original=core.BeginObservation(1,"A","1",1);var decision=core.Adjudicate(Candidate());
            var token=core.Authorize(decision);bool committed=token is not null&&core.Commit(token);
            bool displaysNew=core.CanDisplay(2,"B","2",7,token);
            return Result(decision.ObservationId==original.ObservationId&&token is null&&!committed&&!displaysNew&&core.PoseByMap.Count==0,
                ("originalId",original.ObservationId),("activeId",core.ActiveObservation?.ObservationId),("decisionId",decision.ObservationId),
                ("candidateMap",decision.Candidate?.MapId),("poseMap",decision.Pose?.MapId),("token",token?.Snapshot()),
                ("committed",committed),("displaysNewContext",displaysNew),("poseMaps",core.PoseByMap.Keys.ToArray()));
        });
        foreach(bool reset in new[]{false,true})
        {
            Check("begin-opened-sink-"+(reset?"resets":"begins-new-context"),()=>
            {
                AlignmentCoreV2 core=null!;bool armed=true;long openedId=0;
                core=new(()=>10,(name,payload)=>
                {
                    if(armed&&name=="alignment-v2-observation-opened")
                    {
                        armed=false;openedId=Convert.ToInt64(payload["observationId"]);
                        if(reset)core.ResetRound();else core.BeginObservation(2,"B","2",7);
                    }
                });
                var returned=core.BeginObservation(1,"A","1",1);
                return Result(returned is not null&&returned.ObservationId==openedId&&returned.MapId=="A"
                    &&(reset?core.ActiveObservation is null:core.ActiveObservation?.MapId=="B"),
                    ("openedId",openedId),("returnedId",returned?.ObservationId),("returnedMap",returned?.MapId),("activeMap",core.ActiveObservation?.MapId));
            });
            Check("adjudicate-accepted-sink-"+(reset?"resets":"closes"),()=>
            {
                AlignmentCoreV2 core=null!;bool armed=true;
                core=new(()=>10,(name,payload)=>
                {
                    if(armed&&name=="alignment-v2-terminal-verdict"&&Equals(payload["verdict"],"accepted"))
                    {armed=false;if(reset)core.ResetRound();else core.Close();}
                });
                var original=core.BeginObservation(1,"A","1",1);var decision=core.Adjudicate(Candidate());
                return Result(decision.Accepted&&decision.ObservationId==original.ObservationId&&core.Authorize(decision) is null,
                    ("decisionId",decision.ObservationId),("originalId",original.ObservationId),("activeMap",core.ActiveObservation?.MapId));
            });
            Check("authorize-revoked-sink-"+(reset?"resets":"begins-new-context"),()=>
            {
                AlignmentCoreV2 core=null!;bool armed=false;
                core=new(()=>10,(name,payload)=>
                {
                    if(armed&&name=="alignment-v2-display-revoked"&&Equals(payload["reason"],"new-authorization"))
                    {armed=false;if(reset)core.ResetRound();else core.BeginObservation(2,"B","2",7);}
                });
                var original=core.BeginObservation(1,"A","1",1);var decision=core.Adjudicate(Candidate());
                core.Authorize(decision);armed=true;
                var token=core.Authorize(decision);bool committed=token is not null&&core.Commit(token);
                return Result(token is not null&&token.ObservationId==original.ObservationId&&token.MapId=="A"&&!committed&&core.PoseByMap.Count==0,
                    ("originalId",original.ObservationId),("activeId",core.ActiveObservation?.ObservationId),("token",token?.Snapshot()),
                    ("committed",committed),("poseMaps",core.PoseByMap.Keys.ToArray()));
            });
            Check("close-terminal-sink-"+(reset?"resets":"begins-new-context"),()=>
            {
                AlignmentCoreV2 core=null!;bool armed=true;
                core=new(()=>10,(name,payload)=>
                {
                    if(armed&&name=="alignment-v2-terminal-verdict"&&Equals(payload["verdict"],"closed"))
                    {armed=false;if(reset)core.ResetRound();else core.BeginObservation(2,"B","2",7);}
                });
                var original=core.BeginObservation(1,"A","1",1);bool closed=core.Close();
                var emitted=core.Events.Last(p=>Equals(p["event"],"alignment-v2-observation-closed"));
                var active=core.ActiveObservation;
                return Result(closed&&Equals(emitted["observationId"],original.ObservationId)&&Equals(emitted["mapId"],"A")
                    &&(reset?active is null:active is {MapId:"B",Closed:false}),
                    ("closed",closed),("originalId",original.ObservationId),("closedEvent",emitted),("activeMap",core.ActiveObservation?.MapId));
            });
        }
        Check("update-frame-revoked-sink-begins-new-context",()=>
        {
            AlignmentCoreV2 core=null!;bool armed=false;
            core=new(()=>10,(name,payload)=>
            {
                if(armed&&name=="alignment-v2-display-revoked"&&Equals(payload["reason"],"new-frame"))
                {armed=false;core.BeginObservation(2,"B","2",7);}
            });
            var original=core.BeginObservation(1,"A","1",1);core.Authorize(core.Adjudicate(Candidate()));armed=true;
            bool updated=core.UpdateFrame(2);var ids=core.PendingCandidates.Keys.Order().ToArray();var active=core.ActiveObservation;
            // Python updates the entry observation's pending slot even if the sink replaced active_observation.
            return Result(updated&&original.FrameGeneration==2&&active is {MapId:"B"}
                &&ids.SequenceEqual(new[]{original.ObservationId,active.ObservationId}),
                ("updated",updated),("originalFrame",original.FrameGeneration),("activeMap",core.ActiveObservation?.MapId),("pendingIds",ids));
        });
        Check("commit-sink-begins-new-context",()=>
        {
            AlignmentCoreV2 core=null!;bool armed=true;
            core=new(()=>10,(name,_)=>{if(armed&&name=="alignment-v2-commit"){armed=false;core.BeginObservation(2,"B","2",7);}});
            core.BeginObservation(1,"A","1",1);var token=core.Authorize(core.Adjudicate(Candidate()))!;bool committed=core.Commit(token);
            return Result(committed&&core.PoseByMap.ContainsKey("A")&&!core.PoseByMap.ContainsKey("B")
                &&core.ActiveObservation?.MapId=="B"&&!core.CanDisplay(2,"B","2",7),
                ("committed",committed),("activeMap",core.ActiveObservation?.MapId),("poseMaps",core.PoseByMap.Keys.ToArray()));
        });
        Check("shared-projection-revoked-sink-begins-new-context",()=>
        {
            AlignmentCoreV2 core=null!;bool armed=false;
            core=new(()=>10,(name,payload)=>
            {
                if(armed&&name=="alignment-v2-display-revoked"&&Equals(payload["reason"],"new-authorization"))
                {armed=false;core.BeginObservation(2,"B","2",7);}
            });
            core.SetInitialPose(new("A","source-structure","1",Matrix3.Identity,reliable:true));core.BeginObservation(1,"A","1",1);
            core.AuthorizeSharedProjection();armed=true;var token=core.AuthorizeSharedProjection();
            return Result(token is null&&core.ActiveObservation?.MapId=="B",("token",token?.Snapshot()),("activeMap",core.ActiveObservation?.MapId));
        });
        Check("source-retains-same-observation-revocation-and-frame-boundary",()=>
        {
            var core=new AlignmentCoreV2(()=>10);core.BeginObservation(1,"A","1",1);var decision=core.Adjudicate(Candidate());
            var first=core.Authorize(decision)!;core.RevokeDisplay();var afterRevoke=core.Authorize(decision);
            core.UpdateFrame(2);var afterFrame=core.Authorize(decision);bool committed=afterFrame is not null&&core.Commit(afterFrame);
            return Result(!first.Active&&afterRevoke is not null&&afterFrame?.FrameGeneration==2&&committed,
                ("revokedTokenActive",first.Active),("reauthorizedAfterRevoke",afterRevoke is not null),("afterFrame",afterFrame?.Snapshot()),("committed",committed));
        });
        Check("typed-buffer-copy-preserves-runtime-types",()=>
        {
            var types=new List<Dictionary<string,object?>>();
            Capture(new byte[]{1,2});Capture(new float[]{1,2});Capture(new object?[]{new byte[]{1,2}});
            Capture(new[]{new Memory<byte>(new byte[]{1,2})});
            return Result(types.All(p=>Equals(p["preserved"],true)),("types",types));

            void Capture<T>(T[] backing)
            {
                object[] views={new Memory<T>(backing),new ReadOnlyMemory<T>(backing),new ArraySegment<T>(backing),
                    default(Memory<T>),default(ReadOnlyMemory<T>),default(ArraySegment<T>)};
                foreach(var view in views)
                {
                    var copied=AlignmentData.Copy(view);var recopied=AlignmentData.Copy(copied);
                    types.Add(new(){{"expected",view.GetType().ToString()},{"actual",copied?.GetType().ToString()},
                        {"secondCopy",recopied?.GetType().ToString()},{"preserved",copied?.GetType()==view.GetType()&&recopied?.GetType()==view.GetType()}});
                }
            }
        });
        Check("nested-buffer-evidence-owners",NestedBufferOwners);
        Check("reference-free-values-and-numeric-array-ownership",ReferenceFreeValues);
        Check("unsupported-reference-containing-struct-is-explicit",()=>
        {
            bool direct=Rejected(new ReferencedValue(new byte[]{1,2}));
            bool nested=Rejected(new[]{new ReferencedValue(new byte[]{1,2})});
            return Result(direct&&nested,("directRejected",direct),("arrayElementRejected",nested));
            static bool Rejected(object value){try{AlignmentData.Copy(value);return false;}catch(NotSupportedException){return true;}}
        });
        Check("unsupported-evidence-does-not-partially-write-owners",RejectedEvidenceIsAtomic);
        int failures=results.Count(r=>!Equals(r["passed"],true));
        File.WriteAllText(Path.Combine(output,"alignment-regressions.json"),JsonSerializer.Serialize(new
            {checks=results.Count,failures,scope="real Core sinks and evidence owners; pure state, no Runtime/game acceptance",results},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine(JsonSerializer.Serialize(new{regressionChecks=results.Count,regressionFailures=failures,output}));return failures;

        void Check(string name,Func<Dictionary<string,object?>> action)
        {
            Dictionary<string,object?> result;
            try{result=action();}
            catch(Exception e){result=Result(false,("exception",e.GetType().FullName),("message",e.Message));}
            result["name"]=name;results.Add(result);
            if(!Equals(result["passed"],true))Console.Error.WriteLine("FAIL regression/"+name);
        }
    }

    private static Dictionary<string,object?> NestedBufferOwners()
    {
        var bytes=new byte[]{10,20,30,40};var floats=new float[]{10,20,30,40};var element=new byte[]{5,6};
        var evidence=new Dictionary<string,object?>
        {
            ["memory"]=new[]{bytes.AsMemory(1,2)},["readonly"]=new[]{new ReadOnlyMemory<byte>(bytes,1,2)},
            ["segment"]=new[]{new ArraySegment<byte>(bytes,1,2)},
            ["nested"]=new object?[]{new Memory<byte>[,]{{bytes.AsMemory(1,2)}}},
            ["floatMemory"]=floats.AsMemory(1,2),["floatReadonly"]=new ReadOnlyMemory<float>(floats,1,2),
            ["floatSegment"]=new ArraySegment<float>(floats,1,2),
            ["objectMemory"]=new Memory<object?>(new object?[]{element}),
            ["nestedFloat"]=new[]{new[]{floats.AsMemory(1,2)}}
        };
        var core=new AlignmentCoreV2(()=>10);var group=new RoundLayerGroup("m","source-structure","1",Matrix3.Identity);
        var returned=core.UpdateAnchorPool("m","1",evidence,1,true);group.SetTracking("1",evidence,1);
        bytes[1]=99;floats[1]=99;element[0]=99;
        bool input=Intact(core.AnchorPools[("m","1")])&&Intact(group.TrackingForFloor("1"));
        Mutate(returned);Mutate(core.AnchorPools[("m","1")]);Mutate(group.TrackingForFloor("1"));
        bool output=Intact(core.AnchorPools[("m","1")])&&Intact(group.TrackingForFloor("1"));
        using var json=JsonDocument.Parse(AlignmentData.ToJson(core.AnchorPools[("m","1")])!.ToJsonString());
        bool serialized=json.RootElement.GetProperty("floatMemory")[0].GetDouble()==20
            &&json.RootElement.GetProperty("memory")[0][0].GetInt32()==20&&json.RootElement.GetProperty("objectMemory")[0][0].GetInt32()==5;
        return Result(input&&output&&serialized,("inputIsolated",input),("exportIsolated",output),("typedBuffersSerialize",serialized),
            ("views",new[]{"Memory<byte>[]","ReadOnlyMemory<byte>[]","ArraySegment<byte>[]","object[]/Memory<byte>[,]",
                "Memory<float>","ReadOnlyMemory<float>","ArraySegment<float>","Memory<object>/byte[]","Memory<float>[][]"}));

        static bool Intact(IReadOnlyDictionary<string,object?> data)
        {
            var expected=new byte[]{20,30};var floats=new float[]{20,30};
            var segment=((ArraySegment<byte>[])data["segment"]!)[0];var floatSegment=(ArraySegment<float>)data["floatSegment"]!;
            return ((Memory<byte>[])data["memory"]!)[0].Span.SequenceEqual(expected)
                &&((ReadOnlyMemory<byte>[])data["readonly"]!)[0].Span.SequenceEqual(expected)
                &&segment.AsSpan().SequenceEqual(expected)&&segment.Offset==1
                &&((Memory<byte>[,])((object?[])data["nested"]!)[0]!)[0,0].Span.SequenceEqual(expected)
                &&((Memory<float>)data["floatMemory"]!).Span.SequenceEqual(floats)
                &&((ReadOnlyMemory<float>)data["floatReadonly"]!).Span.SequenceEqual(floats)
                &&floatSegment.AsSpan().SequenceEqual(floats)&&floatSegment.Offset==1
                &&((byte[])((Memory<object?>)data["objectMemory"]!).Span[0]!)[0]==5
                &&((Memory<float>[][])data["nestedFloat"]!)[0][0].Span.SequenceEqual(floats);
        }
        static void Mutate(IReadOnlyDictionary<string,object?> data)
        {
            ((Memory<byte>[])data["memory"]!)[0].Span[0]=77;
            SetReadOnly(((ReadOnlyMemory<byte>[])data["readonly"]!)[0],(byte)77);
            var segment=((ArraySegment<byte>[])data["segment"]!)[0];segment.Array![segment.Offset]=77;
            ((Memory<byte>[,])((object?[])data["nested"]!)[0]!)[0,0].Span[0]=77;
            ((Memory<float>)data["floatMemory"]!).Span[0]=77;
            SetReadOnly((ReadOnlyMemory<float>)data["floatReadonly"]!,77f);
            var floatSegment=(ArraySegment<float>)data["floatSegment"]!;floatSegment.Array![floatSegment.Offset]=77;
            ((byte[])((Memory<object?>)data["objectMemory"]!).Span[0]!)[0]=77;
            ((Memory<float>[][])data["nestedFloat"]!)[0][0].Span[0]=77;
        }
        static void SetReadOnly<T>(ReadOnlyMemory<T> view,T value)
        {if(!MemoryMarshal.TryGetArray(view,out var segment))throw new InvalidOperationException("Expected copied array backing");segment.Array![segment.Offset]=value;}
    }
    private static Dictionary<string,object?> ReferenceFreeValues()
    {
        var numeric=new double[262144];numeric[0]=12;numeric[^1]=34;
        var copied=(double[])AlignmentData.Copy(numeric)!;numeric[0]=99;copied[^1]=88;
        object boxed=new MutableNumber{Value=12};var stored=(IMutableNumber)AlignmentData.Copy(boxed)!;
        ((IMutableNumber)boxed).Value=99;var exported=(IMutableNumber)AlignmentData.Copy(stored)!;exported.Value=88;
        var bounded=Array.CreateInstance(typeof(Memory<byte>),new[]{1},new[]{3});var backing=new byte[]{7,8};bounded.SetValue(backing.AsMemory(),3);
        var boundedCopy=(Array)AlignmentData.Copy(bounded)!;backing[0]=99;
        var mutableClass=new ClassEvidence{Values=new[]{1,2}};var classCopy=(JsonElement)AlignmentData.Copy(mutableClass)!;mutableClass.Values[0]=99;
        bool numericOwned=copied[0]==12&&numeric[^1]==34;
        bool boxedOwned=stored.Value==12;bool bounds=boundedCopy.GetLowerBound(0)==3&&((Memory<byte>)boundedCopy.GetValue(3)!).Span[0]==7;
        bool classOwned=classCopy.GetProperty("Values")[0].GetInt32()==1;
        return Result(numericOwned&&boxedOwned&&bounds&&classOwned,("numericArrayOwned",numericOwned),("boxedValueOwned",boxedOwned),
            ("nonzeroBoundsOwned",bounds),("classNormalizedToOwnedJson",classOwned));
    }
    private static Dictionary<string,object?> RejectedEvidenceIsAtomic()
    {
        var invalid=new ReferencedValue(new byte[]{1,2});var bad=new Dictionary<string,object?>{{"bad",invalid}};
        var core=new AlignmentCoreV2(()=>10);var original=core.BeginObservation(1,"A","1",1);
        var token=core.Authorize(core.Adjudicate(Candidate()))!;int eventCount=core.Events.Count;
        bool beginRejected=Rejected(()=>core.BeginObservation(2,"B","2",7,new object?[]{invalid}));
        bool beginUnchanged=ReferenceEquals(core.ActiveObservation,original)&&!original.Closed&&token.Active&&core.Events.Count==eventCount;
        bool updateRejected=Rejected(()=>core.UpdateFrame(2,"2",new object?[]{invalid}));
        bool updateUnchanged=original.FrameGeneration==1&&original.Floor=="1"&&original.TerminalVerdict=="accepted"&&token.Active&&core.Events.Count==eventCount;
        core.UpdateAnchorPool("A","1",new Dictionary<string,object?>{{"keep",1}},1,true);
        bool anchorRejected=Rejected(()=>core.UpdateAnchorPool("A","1",bad,2,true));
        bool anchorUnchanged=Equals(core.AnchorPools[("A","1")].GetValueOrDefault("keep"),1)&&Equals(core.AnchorPools[("A","1")]["frameGeneration"],1L);
        var group=new RoundLayerGroup("A","source-structure","1",Matrix3.Identity);
        group.SetTracking("1",new Dictionary<string,object?>{{"keep",1}},1);
        bool trackingRejected=Rejected(()=>group.SetTracking("2",bad,2));
        bool trackingUnchanged=group.TrackingFloor=="1"&&group.CycleId==new AlignmentCycleId(1)&&!group.TrackingByFloor.ContainsKey("2")&&Equals(group.Tracking.GetValueOrDefault("keep"),1);
        var oldRelation=Matrix3.Identity.WithTranslation(5,6);group.SetRelation("2",oldRelation,new Dictionary<string,object?>{{"keep",1}});
        bool relationRejected=Rejected(()=>group.SetRelation("2",Matrix3.Identity,bad));
        bool relationUnchanged=ReferenceEquals(group.Relations["2"],oldRelation)&&Equals(group.RelationMetadata["2"].GetValueOrDefault("keep"),1);
        group.MarkDisplayed("1",new byte[]{4,5},false,true);
        bool displayRejected=Rejected(()=>group.MarkDisplayed("2",new[]{invalid},true));
        bool displayUnchanged=group.DisplayedFloor=="1"&&group.DisplayedOverlay is byte[] displayed&&displayed.SequenceEqual(new byte[]{4,5})
            &&group.RenderProfileVerifiedFloors.SequenceEqual(new[]{"1"})&&!group.ProvisionalFloors.Contains("2");
        return Result(beginRejected&&beginUnchanged&&updateRejected&&updateUnchanged&&anchorRejected&&anchorUnchanged
            &&trackingRejected&&trackingUnchanged&&relationRejected&&relationUnchanged&&displayRejected&&displayUnchanged,
            ("beginRejected",beginRejected),("beginUnchanged",beginUnchanged),("updateRejected",updateRejected),("updateUnchanged",updateUnchanged),
            ("anchorRejected",anchorRejected),("anchorUnchanged",anchorUnchanged),("trackingRejected",trackingRejected),("trackingUnchanged",trackingUnchanged),
            ("relationRejected",relationRejected),("relationUnchanged",relationUnchanged),("displayRejected",displayRejected),("displayUnchanged",displayUnchanged));
        static bool Rejected(Action action){try{action();return false;}catch(NotSupportedException){return true;}}
    }
    private static AlignmentCandidate Candidate()=>new("A","1",Matrix3.Identity.WithTranslation(30,-20),"fit",1,currentFrameVerified:true,residualVerified:true);
    private static Dictionary<string,object?> Result(bool passed,params (string Key,object? Value)[] facts)
    {var result=new Dictionary<string,object?>{{"passed",passed}};foreach(var fact in facts)result[fact.Key]=fact.Value;return result;}
    private interface IMutableNumber{int Value{get;set;}}
    private struct MutableNumber:IMutableNumber{public int Value{get;set;}}
    private readonly record struct ReferencedValue(byte[] Bytes);
    private sealed class ClassEvidence{public int[] Values{get;set;}=[];}
}
