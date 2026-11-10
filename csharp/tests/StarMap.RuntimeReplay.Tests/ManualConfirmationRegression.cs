using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class ManualConfirmationRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var rows=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray();
        var index=rows.ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var cases=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        var evidence=new JsonArray();
        foreach(var source in cases)
        {
            string name=source!["name"]!.GetValue<string>(),kind=source["kind"]!.GetValue<string>();
            var capture=new RecordedCapture(index);var runtime=new LiveRuntime(capture,resources,
                kind=="manual-hold"?new(){TimeProvider=new FrozenPolicyClock()}:null);var tickets=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season=source["season"]!.ToString(),mode=source["mode"]!.ToString(),profileId="shared-structure"});
                await Ask("start_round");capture.Select(source["initial"]!.ToString(),false);await Ask("capture_now");
                if(kind=="manual-hold")
                {
                    var pool=runtime.ReadState().RemainingMapIds.ToArray();
                    await Ask("set_floor",new {floor=source["before"]!["manualFloor"]!.ToString()});
                    check(runtime.ReadState().RemainingMapIds.SequenceEqual(pool),name+" reliable floor conflict cannot eliminate candidates from the wrong layer");
                }
                check(runtime.ReadState().MapOpen,name+" has an actual open observation before confirmation");
                if(kind=="closed")await Ask("close_map");
                var before=runtime.ReadState();int calls=capture.Calls;
                if(kind=="open")
                {
                    runtime.TryReadRender(out var previous);var invalid=await Ask("confirm_route",new {mapId=7});runtime.TryReadRender(out var stillCurrent);
                    check(!invalid.Receipt.Success && invalid.Receipt.Reason=="invalid-manual-map" && capture.Calls==calls
                        && runtime.ReadState().Context.MapId==before.Context.MapId && ReferenceEquals(previous,stillCurrent),
                        name+" malformed explicit map id cannot fall back to a selected candidate or revoke its pixels");
                }
                capture.Select(source["input"]!.ToString(),false);
                if(kind.EndsWith("unavailable",StringComparison.Ordinal))capture.FailNext();
                var result=await Ask("confirm_route",new {mapId=source["mapId"]!.ToString()});
                var state=runtime.ReadState();bool displayed=runtime.TryReadRender(out var packet);
                evidence.Add(JsonSerializer.SerializeToNode(new {name,result.Receipt,state.Context,state.IdentityLocked,state.IdentitySource,
                    state.MapOpen,state.ObservedFloor,state.ManualFloor,state.NativeGeometryVerified,captureDelta=capture.Calls-calls,
                    canonical=packet?.Material.Bridge.CanonicalToClient,original=source}));
                await File.WriteAllTextAsync(Path.Combine(output,"manual-confirmation.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
                check(capture.Calls-calls==source["captureDelta"]!.GetValue<int>(),name+" capture count matches the original public confirmation");
                check(state.IdentityLocked && state.IdentitySource=="manual" && state.Context.MapId==source["mapId"]!.ToString(),
                    name+" explicit identity survives fresh capture and any fit refusal");
                string? Normalize(JsonNode? node)=>string.IsNullOrEmpty(node?.ToString())?null:node.ToString();
                var expected=source["after"]!;
                check(result.Receipt.Success==source["result"]!["success"]!.GetValue<bool>() && state.MapOpen==expected["mapOpen"]!.GetValue<bool>()
                    && state.Context.FloorId==Normalize(expected["floor"]) && state.ObservedFloor==Normalize(expected["observedFloor"])
                    && displayed==(expected["render"] is not null),name+" floor, opening, verdict and actual pixel consumer match original");
                if(kind=="manual-hold")check(state.ManualFloor==Normalize(expected["manualFloor"])
                    && result.Receipt.Reason==source["result"]!["reason"]!.ToString()
                    && state.Diagnostics.TryGetProperty("fitAttempted",out var attempted) && attempted.ValueKind==JsonValueKind.False,
                    name+" original unexpired manual hold survives ordinary confirmation and rejects conflicting geometry before fit");
                if(expected["render"]?["canonicalMatrix"] is { } matrix)
                {
                    var wanted=matrix.Deserialize<double[][]>()!;
                    check(packet?.Material.Bridge.CanonicalToClient is { } actual && actual.ToArray().SelectMany(r=>r)
                        .Zip(wanted.SelectMany(r=>r)).All(p=>Math.Abs(p.First-p.Second)<=1e-5),
                        name+" final canonical matrix matches the original newly captured pixels");
                    check(state.Context.FrameId!=before.Context.FrameId && packet!.Stamp.FrameId==state.Context.FrameId,
                        name+" new observation, native pose and material consume the new frame rather than retained pixels");
                }
                else check(!state.NativeGeometryVerified && !state.Display.CanonicalVerified,name+" missing current geometry cannot authorize old material");
                check(!runtime.ReadSnapshot().LiveAuthorization,name+" recorded confirmation cannot grant live authority");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(tickets.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        await Lifetimes();
        await SelectedCandidate();

        async Task Lifetimes()
        {
            foreach(string boundary in new[]{"cancel","invalidate","resource","close","exception"})
            {
                var capture=new RecordedCapture(index);var runtime=new LiveRuntime(capture,resources);var tickets=new SessionSequencer();
                using var cancellation=new CancellationTokenSource();
                try
                {
                    await Ask("set_context",new {season="s1",mode="nightmare",profileId="shared-structure"});await Ask("start_round");
                    capture.Select("batch02-event-000319",false);await Ask("capture_now");
                    capture.Select("batch02-event-000323",false);var hold=capture.HoldNext();
                    if(boundary=="exception")capture.ThrowNext(new IOException("controlled recorded capture exception"));
                    var operation=Ask("confirm_route",new {mapId="107012"},cancellation.Token);
                    try
                    {
                        await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));var pending=runtime.ReadState();
                        check(pending.IdentityLocked && pending.Context.MapId=="107012" && pending.IdentitySource=="manual"
                            && pending.WorkActive && !pending.NativeGeometryVerified && !runtime.TryReadRender(out _),
                            boundary+" identity is visible before blocked capture and before any alignment result");
                        if(boundary=="cancel")cancellation.Cancel();
                        else if(boundary=="resource")runtime.InvalidateResources();
                        else if(boundary!="exception")runtime.InvalidateCapture(boundary);
                    }
                    finally{hold.Release.TrySetResult();}
                    var result=await operation;var state=runtime.ReadState();
                    check(!result.Receipt.Success && !state.WorkActive && !runtime.TryReadRender(out _) && !state.NativeGeometryVerified,
                        boundary+" late confirmation cannot republish prior pixels or pose");
                    check(boundary=="resource"?state.Context.MapId is null:state.Context.MapId=="107012" && state.IdentityLocked,
                        boundary+" preserves the chosen identity until its actual resource/round lifetime ends");
                    if(boundary=="exception")
                    {
                        check(state.MapOpen,"capture exception supplies no negative map-open evidence");
                        var recovered=await Ask("confirm_route",new {mapId="107012"});
                        check(recovered.Receipt.Success && runtime.ReadState().Context.FloorId=="-2" && runtime.TryReadRender(out _),
                            "a new confirmation recovers from capture exception without reopening the game map");
                    }
                }
                finally{runtime.Shutdown();}
                Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                    JsonSerializer.SerializeToElement(payload??new {}),new(tickets.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
            }
        }

        async Task SelectedCandidate()
        {
            var capture=new RecordedCapture(index);var runtime=new LiveRuntime(capture,resources);var tickets=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season="s1",mode="nightmare",profileId="shared-structure"});await Ask("start_round");
                capture.Select("batch02-event-000319",false);await Ask("capture_now");
                var selected=await Ask("select_candidate",new {candidateMapId="107012"});
                check(selected.Receipt.Success && !runtime.ReadState().IdentityLocked,"candidate selection alone does not lock identity or replace confirmation");
                capture.Select("batch02-event-000323",false);int calls=capture.Calls;
                var confirmed=await Ask("confirm_route");runtime.TryReadRender(out var packet);
                check(confirmed.Receipt.Success && capture.Calls==calls+1 && runtime.ReadState().IdentityLocked
                    && runtime.ReadState().Context is {MapId:"107012",FloorId:"-2"} && packet is not null,
                    "no-payload candidate confirmation consumes the selected identity with one fresh target-floor capture");
                await Ask("end_round");await Ask("start_round");capture.Select("batch02-event-000319",false);await Ask("capture_now");
                capture.Select("batch02-event-000400",false);await Ask("refresh_map");
                check(runtime.ReadState() is {MapOpen:true,Context.FloorId:null,IdentityLocked:false} && runtime.ReadState().Candidates.Count>0,
                    "unknown latest floor retains current-opening candidates without identity or pose authority");
                capture.Select("batch02-event-000323",false);calls=capture.Calls;
                var afterUnknown=await Ask("confirm_route");
                check(afterUnknown.Receipt.Success && capture.Calls==calls+1 && runtime.ReadState().Context.FloorId=="-2" && runtime.TryReadRender(out _),
                    "no-payload confirmation with unknown old floor still acquires and authorizes only its new frame");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(tickets.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
    }
    private sealed class FrozenPolicyClock:TimeProvider
    {
        public override long GetTimestamp()=>0;
    }
}
