using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class MapCloseBusyRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var rows=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        foreach(var row in rows)
        {
            string name=row!["name"]!.GetValue<string>();var capture=new RecordedCapture(index);capture.Select(row["sample"]!.GetValue<string>(),false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();RecordedCapture.Hold? hold=null;Task<RuntimeOperationResult>? pending=null;
            try
            {
                await Ask("set_context",new {season=row["season"]!.GetValue<string>(),mode=row["mode"]!.GetValue<string>(),profileId="shared-structure"});
                if(row["manualMap"]?.GetValue<string>() is { } map)
                {await Ask("prepare_manual_alignment");await Ask("confirm_route",new {mapId=map});}
                else{await Ask("start_round");await Ask("capture_now");}
                var before=runtime.ReadState();var oldContext=runtime.PointerContextSlot.Read();
                string action=row["busy"]!["action"]?.GetValue<string>()??"refresh_map";
                hold=capture.HoldNext();pending=Ask(action,action=="confirm_route"?new {mapId=before.Context.MapId}:null);
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                var context=runtime.PointerContextSlot.Read();
                await File.WriteAllTextAsync(Path.Combine(output,name+"-busy.json"),JsonSerializer.Serialize(new {before,during=runtime.ReadState(),context,capture.Calls},new JsonSerializerOptions{WriteIndented=true}));
                check((context is not null)==row["busy"]!["contextAvailable"]!.GetValue<bool>(),name+" original raw pointer observation survives a manual refresh waiting for capture");
                var xy=row["point"]!.AsArray();var press=new GlobalInputPointerEvent("down",(int)xy[0]!.GetValue<double>(),(int)xy[1]!.GetValue<double>(),Now(),1,1,1,1,
                    ContextId:context!.ContextId,Sequence:1,GestureId:1,PressContextSnapshot:context);
                var hit=runtime.ObserveGameFloorClick(press,sequence.Read());
                check(!hit.Success && hit.CloseControlHit==row["busy"]!["currentHit"]!["closeControlHit"]!.GetValue<bool>(),
                    name+" original current X hit stays available without a second capture or floor grant");
                if(action=="confirm_route" || before.ManualAlignmentSession && action!="observe_manual_floor")
                {
                    check(runtime.ReadState().Context.FrameId is null && !runtime.ReadState().NativeGeometryVerified,
                        name+" raw controls remain available while both old numeric frame and pose authority are retired");
                }
                if(action=="confirm_route")
                {
                    var oldPress=press with {ContextId=oldContext!.ContextId,PressContextSnapshot=oldContext};
                    check(!runtime.ObserveGameFloorClick(oldPress,sequence.Read()).CloseControlHit,
                        name+" pre-confirmation press cannot inherit the new raw scope");
                }
                int calls=capture.Calls;runtime.InvalidateCapture("map-closed");
                check(!runtime.ReadState().MapOpen && runtime.ReadState().Context.FloorId==row["immediate"]!["floor"]?.GetValue<string>()
                    && runtime.ReadState().ObservedFloor==row["immediate"]!["observedFloor"]?.GetValue<string>(),
                    name+" current hard close retires manual floor observation immediately");
                hold.Release.TrySetResult();await pending.WaitAsync(TimeSpan.FromSeconds(20));pending=null;
                await Ask("close_map");
                check(capture.Calls==calls && !runtime.ReadState().MapOpen && runtime.ReadState().Context.MapId==before.Context.MapId,
                    name+" late pre-close capture cannot revive a floor or clear the chosen identity");
            }
            finally
            {
                hold?.Release.TrySetResult();runtime.Shutdown();
                if(pending is not null)await pending.WaitAsync(TimeSpan.FromSeconds(20));
            }
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
                new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
    }
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
}
