using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class S0PreparationGateRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(index);capture.Select("batch02-event-000097",false);int transforms=0;
        capture.TransformPixels=(pixels,width,height,stride)=>
        {
            if(++transforms!=1)return;
            // Only the first frame's floor rail is unreadable. Its original
            // door remains visible; the second real frame has no reliable door.
            for(int y=(int)(height*.19);y<(int)(height*.61);y++)
                for(int x=(int)(width*.84);x<(int)(width*.92);x++)
                {int p=y*stride+x*4;pixels[p]=40;pixels[p+1]=32;pixels[p+2]=24;pixels[p+3]=255;}
            capture.Select("batch02-event-000099",false);
        };
        var runtime=new LiveRuntime(capture,resources);var tickets=new SessionSequencer();
        try
        {
            await Ask("set_context",new {season="s0",mode="difficult",profileId="shared-structure"});await Ask("start_round");
            var result=await Ask("map",new {openIntent=true,inputSequence=1});var state=runtime.ReadState();
            bool available=runtime.TryReadRender(out var packet);
            await File.WriteAllTextAsync(Path.Combine(output,"s0-preparation-door-sequence.json"),JsonSerializer.Serialize(new {
                scope="One actual public map preparation; first real frame has only its floor rail masked, second real frame unchanged; no floor/door/identity result injection.",
                capture.Calls,transforms,state,receipt=result.Receipt,available,stamp=packet?.Stamp},new JsonSerializerOptions{WriteIndented=true}));
            check(capture.Calls==2 && transforms==2 && state.MapOpen && state.Context.FloorId=="2",
                "one map preparation consumes the unreadable-floor door frame followed by the actual readable no-door frame");
            check(state.RecognitionPath=="structure-index" && state.RemainingMapIds.Count==30 && state.EliminatedMapIds.Count==0
                && !state.IdentityLocked && state.Context.MapId is null,
                "later preparation probes cannot forget reliable door presence observed earlier in the same request");
            check(!available || packet!.Stamp.FrameId==state.Context.FrameId && state.NativePose?.FloorId=="2",
                "any final preparation material belongs to the final target-floor frame rather than the earlier door frame");
        }
        finally{runtime.Shutdown();}
        Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload??new {}),new(tickets.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
    }
}
