using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class S0SelectionModeRuntimeRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var evidence=new JsonArray();
        foreach(var item in new[]{("difficult","batch02-event-000147"),("nightmare","batch01-event-000006")})
        {
            var capture=new RecordedCapture(index);capture.Select(item.Item2,false);
            var runtime=new LiveRuntime(capture,resources,new(){MapSelectionMode=S0MapSelectionMode.Manual});var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new{season="s0",mode=item.Item1,profileId="shared-structure"});await Ask("start_round");
                var result=await Ask("capture_now");var state=runtime.ReadState();
                evidence.Add(JsonSerializer.SerializeToNode(new{sample=item.Item2,result.Receipt,state,capture.Calls}));
                await File.WriteAllTextAsync(Path.Combine(output,"s0-selection-mode-runtime.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
                check(result.Receipt.Success && state is {MapSelectionMode:"manual",ManualSelectionPending:true,MapOpen:true,Context.MapId:null,Context.FloorId:"1",NativeGeometryVerified:false}
                    && state.Reason=="manual-map-selection-pending" && state.Candidates.Count==0 && state.RemainingMapIds.Count==0
                    && state.RecognitionPath=="ordinary" && runtime.ReadSnapshot().ManualMapPickAvailable && !runtime.TryReadRender(out _),
                    item.Item1+" manual mode reads actual map/floor but bypasses automatic ranking/index/material until explicit choice");
                check(!runtime.ReadSnapshot().AvailableCommands.Contains("confirm_route") && runtime.ReadSnapshot().AvailableCommands.Contains("reselect_map"),
                    item.Item1+" manual picker availability is separate from a nonexistent candidate confirmation");
                if(item.Item1=="difficult")
                {
                    var second=await Take("batch02-event-000099","manual-floor2");
                    check(second.Receipt.Success && runtime.ReadState() is {ManualSelectionPending:true,Context.FloorId:"2",Reason:"manual-map-selection-pending"}
                        && runtime.ReadState().RemainingMapIds.Count==0,"manual unknown identity can observe floor2 without entering the automatic floor1-only recognizer");
                }
                capture.Select(item.Item2,false);string map=item.Item1=="difficult"?"difficult-map-15":"nightmare-12";
                var chosen=await Ask("confirm_route",new{mapId=map});Save("chosen-reference");
                check(chosen.Receipt.Success && runtime.ReadState() is {IdentityLocked:true,IdentitySource:"manual",ManualSelectionPending:false,NativeGeometryVerified:true}
                    && runtime.ReadState().Context.MapId==map && runtime.TryReadRender(out _),
                    item.Item1+" explicit chosen reference follows the existing fresh-frame fit without automatic ranking");
                runtime.TryReadRender(out var beforeMode);var selected=runtime.ReadState();
                runtime.SetMapSelectionMode(S0MapSelectionMode.Auto);runtime.SetMapSelectionMode(S0MapSelectionMode.Manual);
                check(runtime.ReadState().Context==selected.Context && runtime.ReadState().NativePose==selected.NativePose
                    && runtime.TryReadRender(out var afterMode) && ReferenceEquals(beforeMode,afterMode),
                    item.Item1+" changing the preference preserves the chosen identity pose and exact current material");
                var hold=capture.HoldNext();var refit=Ask("capture_now");
                try
                {
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));runtime.SetMapSelectionMode(S0MapSelectionMode.Auto);
                }
                finally{hold.Release.TrySetResult();}
                check((await refit.WaitAsync(TimeSpan.FromSeconds(30))).Receipt.Success && runtime.ReadState() is {IdentityLocked:true,NativeGeometryVerified:true},
                    item.Item1+" an already-selected map continues its in-flight fresh fit when selection preference changes");
                if(item.Item1=="difficult")
                {
                    await Ask("end_round");await Ask("start_round");await Take("batch02-event-000097","auto-door-entry");
                    var automatic=runtime.ReadState();string[] remaining=automatic.RemainingMapIds.ToArray(),excluded=automatic.EliminatedMapIds.ToArray();
                    check(automatic is {MapSelectionMode:"auto",ManualSelectionPending:false,RecognitionPath:"structure-index"}
                        && remaining.Length==26 && runtime.TryReadRender(out _),"automatic preference still enters the actual default S0 index and narrows the round");
                    runtime.SetMapSelectionMode(S0MapSelectionMode.Manual);Save("manual-after-index");
                    check(runtime.ReadState() is {ManualSelectionPending:true,Context.MapId:null,NativeGeometryVerified:false}
                        && runtime.ReadState().RemainingMapIds.SequenceEqual(remaining) && runtime.ReadState().EliminatedMapIds.SequenceEqual(excluded)
                        && !runtime.TryReadRender(out _),"switching an unresolved automatic preview to manual retires its material but retains all round exclusions");
                    await Take("batch02-event-000099","manual-sparse-floor-after-index");
                    check(runtime.ReadState() is {Context.FloorId:"2",Reason:"manual-map-selection-pending"}
                        && runtime.ReadState().RemainingMapIds.SequenceEqual(remaining),"manual cross-floor observation cannot expand the already-entered index pool");
                    runtime.SetMapSelectionMode(S0MapSelectionMode.Auto);await Take("batch02-event-000097","auto-resumed");
                    check(runtime.ReadState() is {ManualSelectionPending:false,RecognitionPath:"structure-index"}
                        && runtime.ReadState().RemainingMapIds.SequenceEqual(remaining) && runtime.TryReadRender(out _),
                        "manual-to-auto resumes fresh indexed matching inside the existing single-round pool");
                    await Ask("end_round");await Ask("start_round");capture.Select("batch02-event-000147",false);
                    var lateHold=capture.HoldNext(ignoreCancellation:true);var oldContext=new SessionRequestContext(sequence.Issue(),runtime.ReadSnapshot().Context);
                    var late=runtime.DispatchAsync(new("capture_now",JsonSerializer.SerializeToElement(new{}),oldContext),default).AsTask();
                    try
                    {
                        await lateHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));runtime.SetMapSelectionMode(S0MapSelectionMode.Manual);
                        check(!runtime.ReadSnapshotFor(oldContext).OwnedByRequest,"policy change retires the old automatic progress owner even when visible context fields were null");
                    }
                    finally{lateHold.Release.TrySetResult();}
                    check(!(await late.WaitAsync(TimeSpan.FromSeconds(30))).Receipt.Success && runtime.ReadState() is {ManualSelectionPending:true,MapOpen:false,Context.MapId:null}
                        && runtime.ReadState().RemainingMapIds.Count==0 && !runtime.TryReadRender(out _),
                        "a deliberately late automatic capture cannot publish identity material or a pool after switching to manual");
                    var sameHold=capture.HoldNext();var same=Ask("capture_now");
                    try{await sameHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));runtime.SetMapSelectionMode(S0MapSelectionMode.Manual);}
                    finally{sameHold.Release.TrySetResult();}
                    check((await same.WaitAsync(TimeSpan.FromSeconds(30))).Receipt.Success && runtime.ReadState().Reason=="manual-map-selection-pending",
                        "reapplying the unchanged preference does not cancel the current manual observation");
                    await Ask("end_round");await Ask("set_context",new{season="s1",mode="nightmare",profileId="shared-structure"});await Ask("start_round");
                    await Take("batch02-event-000315","S1-ignores-S0-manual-preference");runtime.TryReadRender(out var s1Material);var s1=runtime.ReadState();
                    check(s1 is {MapSelectionMode:"auto",ManualSelectionPending:false,RecognitionPath:"structure-index"}
                        && s1.RemainingMapIds.Count>0 && s1Material is not null,"stored S0 manual preference cannot strand S1 automatic structure selection");
                    runtime.SetMapSelectionMode(S0MapSelectionMode.Auto);runtime.SetMapSelectionMode(S0MapSelectionMode.Manual);
                    check(runtime.ReadState().Context==s1.Context && runtime.ReadState().RemainingMapIds.SequenceEqual(s1.RemainingMapIds)
                        && runtime.TryReadRender(out var s1After) && ReferenceEquals(s1After,s1Material),
                        "S0 preference changes cannot retire an S1 identity pool pose or current material");
                    await Ask("end_round");await Ask("set_context",new{season="s0",mode="difficult",profileId="shared-structure"});await Ask("start_round");
                    check(runtime.ReadState() is {MapSelectionMode:"manual",ManualSelectionPending:true,Context.MapId:null}
                        && runtime.ReadState().RemainingMapIds.Count==0,"returning to S0 restores its stored preference but not an earlier round identity or pool");
                }
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new{}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
            async Task<RuntimeOperationResult> Take(string id,string label)
            {capture.Select(id,false);var value=await Ask("capture_now");Save(label);return value;}
            void Save(string label)
            {
                evidence.Add(JsonSerializer.SerializeToNode(new{label,state=runtime.ReadState(),capture.Calls,material=runtime.TryReadRender(out _)}));
                File.WriteAllText(Path.Combine(output,"s0-selection-mode-runtime.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
            }
        }
    }
}
