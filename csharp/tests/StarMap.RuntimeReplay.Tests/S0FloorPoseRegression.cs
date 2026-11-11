using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Runtime;
using StarMap.Vision;

internal static class S0FloorPoseRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
        var runtime=new LiveRuntime(capture,resources);var tickets=new SessionSequencer();
        try
        {
            await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});await Ask("start_round");
            // An explicit manual reference, not an inferred map identity.
            await Ask("confirm_route",new {mapId="nightmare-12"});await Ask("capture_now");
            var state=runtime.ReadState();
            check(state.NativeGeometryVerified && state.Context.FloorId=="1" && runtime.TryReadRender(out _),
                "floor-prior regression starts from an actual committed floor1 fit and canonical material");
            var bound=Field(runtime,"_resources");var group=Field(runtime,"_geometryGroup");
            var catalog=(ResourceCatalog)Property(bound,"Catalog");var passports=(EntryPassportCatalog)Property(bound,"Passports");
            var model=(RoundLayerGroup)Property(group,"Model");var own=state.NativePose!.ToMatrix();
            if(!model.Relations.ContainsKey("2"))model.SetRelation("2",Matrix3.Identity,
                new Dictionary<string,object?>{{"source","simulated-floor-relation-for-state-regression"}});
            var absent=Prior("2");var original=Prior("1");
            // Only this Core floor2 commit is simulated. It uses the real
            // production root writer, but supplies no pixels or solver answer.
            // The following checks invoke actual GroupForFit and public capture.
            var target=model.CanonicalPoseForFloor("2")!.ToArray();
            for(int row=0;row<2;row++)for(int col=0;col<2;col++)target[row][col]*=1.2;
            var adopted=model.AdoptFloorCanonicalPose("2",new(target),"simulated-other-floor-commit",new(17));
            var returned=Prior("1");
            await File.WriteAllTextAsync(Path.Combine(output,"s0-floor-pose-probes.json"),JsonSerializer.Serialize(new {
                scope="Actual recorded floor1 Runtime commit and GroupForFit; only missing floor2 relation and intervening Core root update are simulated. No real floor2 acceptance claim.",
                own,absent,original,adopted,returned,group=model.Snapshot()},new JsonSerializerOptions{WriteIndented=true}));
            check(absent?.CommittedTargetPose is null && absent?.Geometry is null,
                "unverified target floor cannot receive another floor's shared-root geometry or scale seed");
            check(adopted.GetValueOrDefault("success") is true && original?.CommittedTargetPose is not null
                && returned?.CommittedTargetPose is { } saved && Same(saved.Matrix,own)
                && returned.Geometry is { } geometry && Same(geometry.RecognitionMatrixSourceToClient,own),
                "A to simulated B to A retains A's own committed native scale instead of recomputing it from B's root");
            await Ask("capture_now");var result=runtime.ReadState();
            await File.WriteAllTextAsync(Path.Combine(output,"s0-floor-pose-consumed.json"),JsonSerializer.Serialize(result));
            check(result.NativeGeometryVerified && runtime.TryReadRender(out _)
                && result.Diagnostics.GetProperty("stages").EnumerateArray().Any(row=>row.GetProperty("stage").GetString()=="fast-global"
                    && row.GetProperty("outcome").GetString()=="committed-target-floor-scale"),
                "public Runtime capture consumes the target-owned prior in the actual Host and reaches current material");

            S0HostGroupState? Prior(string floor)
            {
                var entry=catalog.Find("nightmare","s0",passports.MapProfiles["nightmare-12"],"nightmare-12",floor)!;
                return (S0HostGroupState?)typeof(LiveRuntime).GetMethod("GroupForFit",BindingFlags.Instance|BindingFlags.NonPublic)!
                    .Invoke(runtime,[Field(runtime,"_epoch"),Field(runtime,"_roundFitOwner"),Field(runtime,"_frame"),bound,entry]);
            }
        }
        finally{runtime.Shutdown();}
        Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload??new {}),new(tickets.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
    }
    private static object Field(object owner,string name)=>owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    private static object Property(object owner,string name)=>owner.GetType().GetProperty(name,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic)!.GetValue(owner)!;
    private static bool Same(double[][] left,double[][] right)=>left.SelectMany(row=>row).Zip(right.SelectMany(row=>row)).All(pair=>Math.Abs(pair.First-pair.Second)<=1e-10);
}
