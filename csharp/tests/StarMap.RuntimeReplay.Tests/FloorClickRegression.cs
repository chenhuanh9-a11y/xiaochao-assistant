using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Floors;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class FloorClickRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var original=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!;
        var originals=original["cases"]!.AsArray();var evidence=new JsonArray();
        foreach(var hit in original["hitCases"]?.AsArray()??new JsonArray())
        {
            var xy=hit!["point"]!.AsArray();var bounds=hit["bounds"]!.AsArray().Select(v=>v!.GetValue<double>()).ToArray();
            int? expected=hit["expected"]?.GetValue<int>();
            check(GameFloorControl.Hit(xy[0]!.GetValue<double>(),xy[1]!.GetValue<double>(),bounds,JsonSerializer.SerializeToElement(hit["read"]))==expected,
                "original-HUD-contract/"+hit["name"]!.GetValue<string>());
        }
        check(GameFloorControl.Hit(double.NaN,0,[0,0,1000,1000],default) is null
            && GameFloorControl.Hit(0,0,[0,0,double.PositiveInfinity,1000],default) is null,"nonfinite-pointer-and-client-bounds-grant-no-floor");
        foreach(var row in originals)
        {
            string mode=row!["mode"]!.GetValue<string>(),kind=row["kind"]!.GetValue<string>(),initial=row["initial"]!.GetValue<string>();
            var clock=new PolicyClock();var capture=new RecordedCapture(index);capture.Select(initial,false);
            var runtime=new LiveRuntime(capture,resources,new(){TimeProvider=clock});var sequence=new SessionSequencer();long pointerSequence=0;
            try
            {
                await Ask("set_context",new {season="s0",mode,profileId="shared-structure"});await Ask("start_round");await Ask("capture_now");
                if(kind=="manual-hold")await Ask("set_floor",new {floor="1"});
                var pressSnapshot=runtime.PointerContextSlot.Read();
                check(pressSnapshot?.DownIntentSnapshot is GameFloorClickContext,mode+" actual captured observation publishes a pixel-free immutable pointer context");
                if(kind=="passive-advance"){clock.Now=1000.05;await Ask("refresh_map");}
                if(kind=="expired-press")clock.Now=1002.01;
                foreach(var step in row["steps"]!.AsArray())
                {
                    if(step!["action"]!.GetValue<string>()=="click")
                    {
                        var snapshot=pressSnapshot!;
                        if(kind=="stale-dpi")
                        {var context=(GameFloorClickContext)snapshot.DownIntentSnapshot!;snapshot=snapshot with {DownIntentSnapshot=context with {Geometry=context.Geometry with {Dpi=context.Geometry.Dpi+1}}};}
                        var point=step["point"]!.AsArray();double created=step["context"]!["createdAt"]!.GetValue<double>();
                        if(kind=="manual-hold")clock.Now=created;
                        if(kind=="ordered" && pointerSequence>0)clock.Now=1000.05;
                        var press=new GlobalInputPointerEvent("down",point[0]!.GetValue<int>(),point[1]!.GetValue<int>(),created,1,1,1,1,
                            ContextId:snapshot.ContextId,Sequence:++pointerSequence,GestureId:pointerSequence,PressContextSnapshot:snapshot);
                        if(pointerSequence==1)
                        {
                            var before=runtime.ReadState();var closed=runtime.ObserveGameFloorClick(press,sequence.Read() with {Closed=true});
                            check(!closed.Success && ReferenceEquals(before,runtime.ReadState()),row["name"]+" a closed Session grants no click lifetime or mutation");
                        }
                        var result=runtime.ObserveGameFloorClick(press,sequence.Read());
                        check(result.Success==step["result"]!["success"]!.GetValue<bool>() && result.Reason==step["result"]!["reason"]!.GetValue<string>(),
                            row["name"]+" original current-scope hit/stale/expiry decision");
                        if(result.Success)
                            check(!runtime.TryReadRender(out _) && runtime.ReadState().MapOpen && runtime.ReadState().FloorClickIntent is not null,
                                row["name"]+" accepted click withdraws pose while preserving the observed map cycle");
                    }
                    else
                    {
                        if(kind=="expired-intent")clock.Now=1002.01;
                        if(step["now"] is { } at)clock.Now=at.GetValue<double>();
                        capture.Select(step["input"]!.GetValue<string>(),false);int before=capture.Calls;
                        if(kind=="capture-error")capture.ThrowNext(new IOException("isolated recorded capture failure"));
                        if(kind=="capture-empty")capture.FailNext();
                        var result=await Ask("refresh_map");
                        check(capture.Calls==before+1 && result.Receipt.Success==step["result"]!["success"]!.GetValue<bool>(),row["name"]+" actual refresh captures exactly once and preserves the original fit verdict");
                        if(step["result"]!["reason"]?.GetValue<string>()=="current-frame-floor-conflict")
                            check(result.Receipt.Reason=="current-frame-floor-conflict" && !runtime.TryReadRender(out _),row["name"]+" original manual hold blocks conflicting pixels before fit");
                    }
                    var state=runtime.ReadState();var expected=step["state"]!;
                    check(state.Context.FloorId==expected["floor"]?.GetValue<string>() && state.ObservedFloor==expected["observedFloor"]?.GetValue<string>()
                        && state.MapOpen==expected["mapOpen"]!.GetValue<bool>(),row["name"]+" original target/observed/open state after each actual step");
                    check((state.FloorClickIntent is not null)==(expected["intent"] is JsonObject),row["name"]+" original pending intent consumption");
                    if(kind=="manual-hold")
                        check(state.ManualFloor==expected["manualFloor"]?.GetValue<string>() && state.FloorPolicyReason==expected["policy"]!.GetValue<string>(),
                            row["name"]+" explicit hint and original hold policy remain distinct");
                    if(kind=="mismatch" && step["action"]!.GetValue<string>()=="refresh_map")
                        check(!runtime.TryReadRender(out _) && state.FloorPolicyReason=="pending-click-mismatch",row["name"]+" mismatched highlighted pixels grant no fit or display");
                }
                evidence.Add(JsonSerializer.SerializeToNode(new {name=row["name"]!.GetValue<string>(),state=runtime.ReadState(),capture.Calls}));
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        foreach(string season in new[]{"s0","s1"})
        {
            var capture=new RecordedCapture(index);capture.Select(season=="s0"?"batch01-event-000006":"batch02-event-000315",false);
            var clock=new PolicyClock();var runtime=new LiveRuntime(capture,resources,new(){TimeProvider=clock});var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season,mode="nightmare",profileId="shared-structure"});await Ask("start_round");await Ask("capture_now");
                var snapshot=runtime.PointerContextSlot.Read();
                check(snapshot?.DownIntentSnapshot is GameFloorClickContext,season+" real recorded observation provides current pointer metadata");
                var context=(GameFloorClickContext)snapshot!.DownIntentSnapshot!;
                var press=new GlobalInputPointerEvent("down",context.Geometry.Left+context.Geometry.Width/2,context.Geometry.Top+context.Geometry.Height/2,clock.Now,1,1,1,1,
                    ContextId:snapshot.ContextId,Sequence:1,GestureId:1,PressContextSnapshot:snapshot);
                var before=runtime.ReadState();
                check(runtime.ObserveGameFloorClick(press,sequence.Read()).Reason=="not-floor-control" && ReferenceEquals(before,runtime.ReadState()),
                    season+" unsupported or missed HUD geometry mutates no candidate, pose or floor state");
                await Ask("close_map");
                check(runtime.PointerContextSlot.Read() is null && !runtime.ObserveGameFloorClick(press,sequence.Read()).Success,
                    season+" a real close retires the slot and rejects the old press");
                await Ask("capture_now");await Ask("start_round");
                check(!runtime.ObserveGameFloorClick(press,sequence.Read()).Success && runtime.ReadState().FloorClickIntent is null,
                    season+" a new round cannot borrow the previous observation or click span");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        await File.WriteAllTextAsync(Path.Combine(output,"floor-click-cases.json"),evidence.ToJsonString(new(){WriteIndented=true}));
    }
    sealed class PolicyClock:TimeProvider
    {
        public double Now=1000;
        public override long TimestampFrequency=>1000;
        public override long GetTimestamp()=>(long)Math.Round(Now*1000);
    }
}
