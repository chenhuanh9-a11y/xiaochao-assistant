using System.Text.Json;
using System.Text.Json.Nodes;
using System.Runtime.InteropServices;
using StarMap.Core.Alignment;

internal static class Program
{
    private static readonly JsonSerializerOptions Pretty=new(){WriteIndented=true};
    private static int Main(string[] args)
    {
        if(args.Length!=2){Console.Error.WriteLine("Usage: StarMap.Alignment.Tests <python-oracle.json|--regressions-only> <new-evidence-directory>");return 1;}
        string output=Path.GetFullPath(args[1]);
        if(Directory.Exists(output)&&Directory.EnumerateFileSystemEntries(output).Any()){Console.Error.WriteLine("Use a new empty evidence directory.");return 1;}
        Directory.CreateDirectory(output);
        var failures=new List<object>();int count=0;
        try
        {
            if(args[0]=="--regressions-only")return AlignmentRegressionChecks.Run(output)==0?0:1;
            using var document=JsonDocument.Parse(File.ReadAllBytes(args[0]));
            foreach(var test in document.RootElement.GetProperty("coreCases").EnumerateArray())
            {
                double time=10;int sinkCount=0;IReadOnlyDictionary<string,object?>? sinkLast=null;
                var core=new AlignmentCoreV2(()=>time,(_,payload)=>{sinkCount++;sinkLast=AlignmentData.ReadOnly(payload);},Strings(test.GetProperty("floors")));
                var decisions=new Dictionary<string,Adjudication>();var tokens=new Dictionary<string,DisplayAuthorization?>();
                int index=0;
                foreach(var step in test.GetProperty("steps").EnumerateArray())
                {
                    var op=step.GetProperty("operation");object? result=null;
                    try
                    {
                        string name=S(op,"op");
                        switch(name)
                        {
                            case "begin":result=ObservationView(core.BeginObservation(Cycle(op,"cycle",1),S(op,"mapId","m"),S(op,"floor","1"),L(op,"frame",1),
                                Values(op,"signature"),S(op,"reason","open"),NullableNumber(op,"now")));break;
                            case "update":result=core.UpdateFrame(L(op,"frame",1),OptionalString(op,"floor"),Values(op,"signature"));break;
                            case "stage":result=core.RecordStage(S(op,"name"),N(op,"elapsed"));break;
                            case "seed":result=core.SetInitialPose(Pose(Property(op,"pose")),S(op,"reason","initial"));break;
                            case "adjudicate":var d=core.Adjudicate(Candidate(Property(op,"candidate")));decisions[S(op,"save","d")]=d;result=DecisionView(d);break;
                            case "authorize":var t=core.Authorize(decisions[S(op,"decision","d")],OptionalString(op,"source"),NullableNumber(op,"now"));tokens[S(op,"save","t")]=t;result=t?.Snapshot();break;
                            case "commit":result=core.Commit(tokens[S(op,"token","t")]!,B(op,"persist",true));break;
                            case "can":result=core.CanDisplay(Cycle(op,"cycle",1),S(op,"mapId","m"),S(op,"floor","1"),L(op,"frame",1),op.TryGetProperty("token",out var tk)?tokens[tk.GetString()!]:null);break;
                            case "continuity":result=core.ContinuityCurrent(Cycle(op,"cycle",1),S(op,"mapId","m"),S(op,"floor","1"),L(op,"frame",1),op.TryGetProperty("token",out var ct)?tokens[ct.GetString()!]:null);break;
                            case "revoke":core.RevokeDisplay(S(op,"reason","hidden"));break;
                            case "close":result=op.TryGetProperty("cycle",out var closeCycle)
                                ? core.Close(Cycle(closeCycle),S(op,"reason","map-closed"))
                                : core.Close(reason:S(op,"reason","map-closed"));break;
                            case "reset":core.ResetRound(S(op,"reason","round-reset"));break;
                            case "clock":time=N(op,"value");result=time;break;
                            case "anchors":result=core.UpdateAnchorPool(S(op,"mapId","m"),S(op,"floor","1"),Map(Property(op,"evidence")),L(op,"frame",1),B(op,"success",true));break;
                            case "cache":result=core.CacheCandidate(Candidate(Property(op,"candidate")),S(op,"reason","late-background-result"));break;
                            case "shared":var sh=core.AuthorizeSharedProjection();tokens[S(op,"save","t")]=sh;result=sh?.Snapshot();break;
                            case "provisional":var pr=core.AuthorizeProvisionalTarget();tokens[S(op,"save","t")]=pr;result=pr?.Snapshot();break;
                            case "record":var re=core.RecordVerifiedDisplay(S(op,"mapId","m"),S(op,"floor","1"),Matrix(op,"matrix",DefaultPose),S(op,"source","verified-compositor"),L(op,"frame",1),B(op,"verified",true));tokens[S(op,"save","t")]=re;result=re?.Snapshot();break;
                            case "preserve":result=core.PreserveEquivalentDisplay();break;
                            default:throw new ArgumentException("Unknown core operation: "+name);
                        }
                    }
                    catch(Exception e)when(e is ArgumentException or ArithmeticException or InvalidOperationException){result=new Dictionary<string,object?>{{"argumentError",true}};}
                    var state=(Dictionary<string,object?>)CoreState(core);state["eventSinkCount"]=sinkCount;state["eventSinkLast"]=sinkLast;
                    var actual=new Dictionary<string,object?>{{"operation",AlignmentData.FromJson(op)},{"result",result},{"state",state}};
                    Check("core",S(test,"name"),index++,step,AlignmentData.ToJson(actual));
                }
            }
            foreach(var test in document.RootElement.GetProperty("groupCases").EnumerateArray())
            {
                var initial=test.GetProperty("initial");var g=new RoundLayerGroup("m",S(initial,"profile","source-structure"),"1",Matrix(initial,"matrix",DefaultPose),
                    Relations(Property(initial,"relations")),poseClientSize:Ints(initial,"clientSize"),poseDisplaySignature:Values(initial,"signature"));
                var adapters=new FixedAdapters(Property(test,"adapters"));int index=0;
                foreach(var step in test.GetProperty("steps").EnumerateArray())
                {
                    var op=step.GetProperty("operation");object? result=null;
                    try
                    {
                        switch(S(op,"op"))
                        {
                            case "relation":result=g.SetRelation(S(op,"floor"),Matrix(op,"matrix",Matrix3.Identity),op.TryGetProperty("metadata",out var meta)?Map(meta):null);break;
                            case "canonical":result=g.CanonicalPoseForFloor(S(op,"floor"));break;
                            case "profile":result=g.ProfilePoseForFloor(B(op,"noAdapters")?null:adapters,S(op,"floor"));break;
                            case "recognition":result=g.RecognitionPoseForFloor(B(op,"noAdapters")?null:adapters,S(op,"floor"));break;
                            case "translate":result=g.ApplyTranslation(N(op,"dx"),N(op,"dy"),S(op,"source","drag"),Cycle(op,"cycle",1));break;
                            case "reproject":result=g.ReprojectClientPose(Ints(op,"size"),S(op,"source","resize"),Cycle(op,"cycle",1));break;
                            case "adopt":result=g.AdoptFloorCanonicalPose(S(op,"floor"),Matrix(op,"matrix",Matrix3.Identity),S(op,"source","fit"),Cycle(op,"cycle",1),N(op,"maximum",.65));break;
                            case "tracking":g.SetTracking(S(op,"floor"),Map(Property(op,"evidence")),Cycle(op,"cycle",1));break;
                            case "trackget":result=g.TrackingForFloor(S(op,"floor"));break;
                            case "display":g.MarkDisplayed(S(op,"floor"),op.TryGetProperty("pixels",out var pixels)?(Array)PlainArray(pixels):new object?[]{new[]{0,1},new[]{2,3}},B(op,"provisional"),B(op,"render"),B(op,"canonical"),B(op,"source"));break;
                            default:throw new ArgumentException("Unknown group operation");
                        }
                    }
                    catch(Exception e)when(e is ArgumentException or ArithmeticException or InvalidOperationException){result=new Dictionary<string,object?>{{"argumentError",true}};}
                    var actual=new Dictionary<string,object?>{{"operation",AlignmentData.FromJson(op)},{"result",result},
                        {"state",new Dictionary<string,object?>{{"snapshot",g.Snapshot()},{"relations",g.Relations.ToDictionary(p=>p.Key,p=>(object?)p.Value)},
                            {"relationMetadata",g.RelationMetadata.ToDictionary(p=>p.Key,p=>(object?)p.Value)}, {"tracking",g.Tracking},
                            {"trackingByFloor",g.TrackingByFloor.ToDictionary(p=>p.Key,p=>(object?)p.Value)},{"overlay",g.DisplayedOverlay}}}};
                    Check("group",S(test,"name"),index++,step,AlignmentData.ToJson(actual));
                }
            }
            foreach(var test in document.RootElement.GetProperty("evidenceCases").EnumerateArray())
            {
                var residual=Property(test,"residual");IReadOnlyDictionary<string,object?>? data=residual.ValueKind==JsonValueKind.Object?Map(residual):null;
                var actual=new Dictionary<string,object?>();
                if(test.TryGetProperty("wrapped",out var expectedWrapped))
                {
                    data=AlignmentObservationEvidence.CanonicalResidualEvidence(data!,S(test,"basis"),S(test,"pack"),B(test,"bridge"));
                    actual["wrapped"]=data;
                }
                actual["verified"]=AlignmentObservationEvidence.RenderedProfileResidualIsVerified(data);
                var expected=new JsonObject{{"verified",test.GetProperty("verified").GetBoolean()}};
                if(test.TryGetProperty("wrapped",out var wrapped))expected["wrapped"]=JsonNode.Parse(wrapped.GetRawText());
                Check("evidence",S(test,"name"),0,JsonSerializer.SerializeToElement(expected),AlignmentData.ToJson(actual));
            }
            DefensiveCopies();
            int regressionFailures=AlignmentRegressionChecks.Run(output);
            if(regressionFailures>0)failures.Add(new{kind="regressions",failures=regressionFailures});
            File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(new{checks=count,failures,scope="pure source differential; no runtime/game authorization acceptance"},Pretty));
            Console.WriteLine(JsonSerializer.Serialize(new{checks=count,failures=failures.Count,output}));return failures.Count==0?0:1;

            void Check(string kind,string name,int step,JsonElement expected,JsonNode? actual)
            {
                count++;var record=new JsonObject{{"kind",kind},{"name",name},{"step",step},{"actual",actual?.DeepClone()}};
                File.WriteAllText(Path.Combine(output,$"{count:D4}-{kind}.json"),record.ToJsonString(Pretty));
                string? difference=Compare(JsonNode.Parse(expected.GetRawText()),actual,"$");
                if(difference is not null){failures.Add(new{kind,name,step,difference});Console.Error.WriteLine($"FAIL {kind}/{name}/{step}: {difference}");}
            }
            void DefensiveCopies()
            {
                var sourceMatrix=DefaultPose.ToArray();var finite=new Matrix3(sourceMatrix);sourceMatrix[0][0]=99;
                var returnedMatrix=finite.ToArray();returnedMatrix[0][1]=88;
                if(finite[0,0]!=.4||finite[0,1]!=0)throw new InvalidOperationException("Matrix aliases mutable input/output rows");
                var input=new Dictionary<string,object?>{{"independentFloorAnchor",true},{"nested",new Dictionary<string,object?>{{"array",new[]{1,2}}}}};
                var c=new AlignmentCandidate("m","1",DefaultPose,"initial-structure-v2",1,metadata:input);
                ((int[])((Dictionary<string,object?>)input["nested"]!)["array"]!)[0]=99;
                var external=(Dictionary<string,object?>)c.Metadata["nested"]!;((int[])external["array"]!)[1]=88;
                var retained=(Dictionary<string,object?>)c.Metadata["nested"]!;
                if(!((int[])retained["array"]!).SequenceEqual(new[]{1,2}))throw new InvalidOperationException("Candidate metadata aliases external dictionaries/arrays");
                var a=new AlignmentCoreV2(()=>10);var evidence=new Dictionary<string,object?>{{"mask",new byte[,]{{0,1},{1,0}}}};
                a.UpdateAnchorPool("m","1",evidence,1,true);((byte[,])evidence["mask"]!)[0,0]=9;
                ((byte[,])a.AnchorPools[("m","1")]["mask"]!)[1,1]=9;
                if(((byte[,])a.AnchorPools[("m","1")]["mask"]!)[0,0]!=0||((byte[,])a.AnchorPools[("m","1")]["mask"]!)[1,1]!=0)
                    throw new InvalidOperationException("Anchor evidence aliases input/output masks");
                var g=new RoundLayerGroup("m","source-structure","1",DefaultPose);g.SetTracking("1",evidence,1);
                ((byte[,])evidence["mask"]!)[0,1]=8;((byte[,])g.TrackingForFloor("1")["mask"]!)[1,0]=8;
                if(((byte[,])g.TrackingForFloor("1")["mask"]!)[0,1]!=1||((byte[,])g.TrackingForFloor("1")["mask"]!)[1,0]!=1)
                    throw new InvalidOperationException("Round tracker aliases external masks");
                var pixels=new byte[,]{{1,2},{3,4}};g.MarkDisplayed("1",pixels,false);pixels[0,0]=9;((byte[,])g.DisplayedOverlay!)[1,1]=9;
                if(((byte[,])g.DisplayedOverlay!)[0,0]!=1||((byte[,])g.DisplayedOverlay!)[1,1]!=4)throw new InvalidOperationException("Displayed overlay aliases external pixels");
                a.BeginObservation(1,"m","1",1);var adjudication=a.Adjudicate(new("m","1",DefaultPose,"fit",1,currentFrameVerified:true,residualVerified:true));
                var authorization=a.Authorize(adjudication)!;var authorizationSnapshot=authorization.Snapshot();authorizationSnapshot["active"]=false;
                if(!authorization.Active)throw new InvalidOperationException("Authorization snapshot mutated core token");
                a.RevokeDisplay();if(authorization.Active||a.Commit(authorization))throw new InvalidOperationException("Revoked token handle remained usable");
                var backing=new byte[]{10,20,30,40};
                var bufferEvidence=new Dictionary<string,object?>{{"memory",backing.AsMemory(1,2)},
                    {"readonly",new ReadOnlyMemory<byte>(backing,1,2)},{"segment",new ArraySegment<byte>(backing,1,2)}};
                var updateOutput=a.UpdateAnchorPool("buffers","1",bufferEvidence,1,true);
                g.SetTracking("2",bufferEvidence,1);
                backing[1]=99;
                CheckBuffers(a.AnchorPools[("buffers","1")]);CheckBuffers(g.TrackingForFloor("2"));
                MutateBuffers(updateOutput);MutateBuffers(a.AnchorPools[("buffers","1")]);MutateBuffers(g.TrackingForFloor("2"));
                CheckBuffers(a.AnchorPools[("buffers","1")]);CheckBuffers(g.TrackingForFloor("2"));
                File.WriteAllText(Path.Combine(output,"defensive-copies.json"),"{\"matrixRows\":true,\"candidateMetadata\":true,\"anchorMasks\":true,\"trackingMasks\":true,\"overlayPixels\":true,\"authorizationSnapshot\":true,\"memoryBuffers\":true,\"readOnlyMemoryBuffers\":true,\"arraySegments\":true}");

                static void CheckBuffers(IReadOnlyDictionary<string,object?> stored)
                {
                    var expected=new byte[]{20,30};
                    if(!((Memory<byte>)stored["memory"]!).Span.SequenceEqual(expected)
                        ||!((ReadOnlyMemory<byte>)stored["readonly"]!).Span.SequenceEqual(expected)
                        ||!((ArraySegment<byte>)stored["segment"]!).AsSpan().SequenceEqual(expected)
                        ||((ArraySegment<byte>)stored["segment"]!).Offset!=1)
                        throw new InvalidOperationException("Stored anchor/tracking evidence aliases a caller buffer");
                }
                static void MutateBuffers(IReadOnlyDictionary<string,object?> exported)
                {
                    ((Memory<byte>)exported["memory"]!).Span[0]=77;
                    if(!MemoryMarshal.TryGetArray((ReadOnlyMemory<byte>)exported["readonly"]!,out var array)||array.Array is null)
                        throw new InvalidOperationException("Expected owned read-only buffer array");
                    array.Array[array.Offset]=88;
                    var segment=(ArraySegment<byte>)exported["segment"]!;segment.Array![segment.Offset]=66;
                }
            }
        }
        catch(Exception e){File.WriteAllText(Path.Combine(output,"fatal.json"),JsonSerializer.Serialize(new{message=e.Message,type=e.GetType().FullName},Pretty));Console.Error.WriteLine(e.Message);return 1;}
    }
    private static readonly Matrix3 DefaultPose=new(new[]{new[]{.4,0,30},new[]{0d,.5,-20},new[]{.001,-.002,1}});
    private static JsonElement Property(JsonElement e,string key)=>e.ValueKind==JsonValueKind.Object&&e.TryGetProperty(key,out var p)?p:default;
    private static string S(JsonElement e,string key,string fallback="")=>Property(e,key) is {ValueKind:JsonValueKind.String} p?p.GetString()!:fallback;
    private static string? OptionalString(JsonElement e,string key)=>Property(e,key) is {ValueKind:JsonValueKind.String} p?p.GetString():null;
    private static bool B(JsonElement e,string key,bool fallback=false)=>Property(e,key) is {ValueKind:JsonValueKind.True}?true:Property(e,key) is {ValueKind:JsonValueKind.False}?false:fallback;
    private static double N(JsonElement e,string key,double fallback=0)=>Property(e,key) is {ValueKind:JsonValueKind.Number} p?p.GetDouble():fallback;
    private static long L(JsonElement e,string key,long fallback=0)=>Property(e,key) is {ValueKind:JsonValueKind.Number} p?p.GetInt64():fallback;
    private static double? NullableNumber(JsonElement e,string key)=>Property(e,key) is {ValueKind:JsonValueKind.Number} p?p.GetDouble():null;
    private static string[] Strings(JsonElement e)=>e.EnumerateArray().Select(n=>n.GetString()!).ToArray();
    private static int[]? Ints(JsonElement e,string key)=>Property(e,key) is {ValueKind:JsonValueKind.Array} p?p.EnumerateArray().Select(n=>n.GetInt32()).ToArray():null;
    private static object?[] Values(JsonElement e,string key)=>Property(e,key) is {ValueKind:JsonValueKind.Array} p?PlainArray(p):[];
    private static object?[] PlainArray(JsonElement p)=>p.EnumerateArray().Select(v=>v.ValueKind==JsonValueKind.Array?(object?)PlainArray(v):v.ValueKind==JsonValueKind.Object?Map(v):AlignmentData.FromJson(JsonSerializer.SerializeToElement(new Dictionary<string,JsonElement>{{"value",v}}))["value"]).ToArray();
    private static Dictionary<string,object?> Map(JsonElement p)=>p.ValueKind==JsonValueKind.Object?AlignmentData.FromJson(p):[];
    private static AlignmentCycleId Cycle(JsonElement p)=>p.ValueKind==JsonValueKind.String?new(p.GetString()!):new(p.GetInt64());
    private static AlignmentCycleId Cycle(JsonElement p,string key,long fallback)=>p.TryGetProperty(key,out var v)?Cycle(v):new(fallback);
    private static Matrix3 Matrix(JsonElement p,string key,Matrix3 fallback)=>Property(p,key) is {ValueKind:JsonValueKind.Array} m?
        Matrix3.TryRead(m)??throw new ArgumentException("invalid matrix"):fallback;
    private static Dictionary<string,Matrix3> Relations(JsonElement p)=>p.ValueKind==JsonValueKind.Object?p.EnumerateObject().ToDictionary(v=>v.Name,v=>Matrix3.TryRead(v.Value)??throw new ArgumentException("invalid relation")):[];
    private static RoundPose Pose(JsonElement p)=>new(S(p,"mapId","m"),S(p,"routeProfileId","shared-structure"),S(p,"anchorFloor","1"),Matrix(p,"matrix",DefaultPose),
        Relations(Property(p,"relations")),L(p,"generation"),B(p,"reliable",true),S(p,"source","seed"),Property(p,"floors") is {ValueKind:JsonValueKind.Array} f?Strings(f):null);
    private static AlignmentCandidate Candidate(JsonElement p)=>new(S(p,"mapId","m"),S(p,"floor","1"),Matrix(p,"matrix",DefaultPose),S(p,"source","initial-structure-v2"),L(p,"frame",1),
        N(p,"confidence"),(int)L(p,"anchorCount"),S(p,"anchorKind","global"),B(p,"current"),B(p,"residual"),B(p,"global"),B(p,"translationOnly"),
        B(p,"crossFloor"),B(p,"deterministic"),B(p,"localCorrection"),B(p,"identityRerank"),B(p,"explicitDiscontinuity"),Map(Property(p,"metadata")));
    private static object ObservationView(Observation o)=>new Dictionary<string,object?>{{"observationId",o.ObservationId},{"cycleId",o.CycleId.Value},{"mapId",o.MapId},{"floor",o.Floor},
        {"frameGeneration",o.FrameGeneration},{"openedAt",o.OpenedAt},{"frameSignature",o.FrameSignature},{"stages",o.Stages.ToDictionary(p=>p.Key,p=>(object?)p.Value)},
        {"terminalVerdict",o.TerminalVerdict},{"terminalReason",o.TerminalReason},{"closed",o.Closed}};
    private static object DecisionView(Adjudication d)=>new Dictionary<string,object?>{{"accepted",d.Accepted},{"reason",d.Reason},{"observationId",d.ObservationId},{"pose",d.Pose?.Snapshot()},{"terminal",d.Terminal}};
    private static object CoreState(AlignmentCoreV2 core)=>new Dictionary<string,object?>{{"snapshot",core.Snapshot()},{"authorization",core.DisplayAuthorization?.Snapshot()},
        {"anchors",core.AnchorPools.OrderBy(p=>p.Key.Map,StringComparer.Ordinal).ThenBy(p=>p.Key.Floor,StringComparer.Ordinal).Select(p=>new Dictionary<string,object?>{{"key",new[]{p.Key.Map,p.Key.Floor}},{"value",p.Value}}).ToArray()},
        {"cache",core.CandidateCache.OrderBy(p=>p.Key.Map,StringComparer.Ordinal).ThenBy(p=>p.Key.Floor,StringComparer.Ordinal).Select(p=>new Dictionary<string,object?>{{"key",new[]{p.Key.Map,p.Key.Floor}},
            {"frames",p.Value.Select(c=>c.FrameGeneration).ToArray()},{"sources",p.Value.Select(c=>c.Source).ToArray()}}).ToArray()},
        {"pending",core.PendingCandidates.OrderBy(p=>p.Key).ToDictionary(p=>p.Key.ToString(),p=>(object?)p.Value.Select(c=>c.Source).ToArray())},{"events",core.Events}};
    private sealed class FixedAdapters(JsonElement data):IRoundCoordinateAdapters
    {
        public Matrix3? ComposeProfileToClientFromCanonical(string profile,string mapId,string floor,Matrix3 canonicalToClient)=>Project("profile",floor,canonicalToClient);
        public Matrix3? RecognitionFromCanonicalToClient(string mapId,string floor,Matrix3 canonicalToClient)=>Project("recognition",floor,canonicalToClient);
        private Matrix3? Project(string kind,string floor,Matrix3 root)=>Property(Property(data,kind),floor) is {ValueKind:JsonValueKind.Array} p&&Matrix3.TryRead(p) is { } m?Matrix3.Multiply(root,m):null;
    }
    private static string? Compare(JsonNode? expected,JsonNode? actual,string path)
    {
        if(expected is null||actual is null)return expected is null&&actual is null?null:path+": null mismatch";
        if(expected is JsonObject eo&&actual is JsonObject ao)
        {if(!eo.Select(p=>p.Key).Order().SequenceEqual(ao.Select(p=>p.Key).Order()))return path+": object keys differ";
            foreach(var p in eo){var difference=Compare(p.Value,ao[p.Key],path+"."+p.Key);if(difference is not null)return difference;}return null;}
        if(expected is JsonArray ea&&actual is JsonArray aa)
        {if(ea.Count!=aa.Count)return path+": array length differs";for(int i=0;i<ea.Count;i++){var difference=Compare(ea[i],aa[i],path+"["+i+"]");if(difference is not null)return difference;}return null;}
        using var ed=JsonDocument.Parse(expected.ToJsonString());using var ad=JsonDocument.Parse(actual.ToJsonString());
        if(ed.RootElement.ValueKind==JsonValueKind.Number&&ad.RootElement.ValueKind==JsonValueKind.Number)
        {double e=ed.RootElement.GetDouble(),a=ad.RootElement.GetDouble();return Math.Abs(e-a)<=1e-9+Math.Abs(e)*1e-9?null:$"{path}: expected {e:R}, actual {a:R}";}
        return JsonNode.DeepEquals(expected,actual)?null:path+": value differs";
    }
}
