using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

// Only OS capture is replaced. The independent index report is read for
// comparison after solving; no expected map, transform or pool enters Runtime.
internal static class S0StructureIndexRuntimeRegression
{
    internal static async Task Run(string resources,string replay,string indexOracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(index);var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
        var evidence=new JsonArray();
        try
        {
            string markerPath=Path.Combine(AppContext.BaseDirectory,"s1-private-assets","s0_static_exit_markers.json");
            check(File.Exists(markerPath),"default output supplies the portable S0 marker resource");
            var marker=JsonNode.Parse(await File.ReadAllBytesAsync(markerPath))!;
            check(marker["records"]!.AsArray().Count==29
                && marker["records"]!.AsArray().Sum(row=>row!["marks"]!.AsArray().Count)==87,
                "packaged derived resource contains all29 source-bound layouts and87 door observations");
            check((await Ask("set_context",new {season="s0",mode="difficult",profileId="shared-structure"})).Receipt.Success,
                "actual default S0 difficult resources are bound");
            await Ask("start_round");var started=Save("started");string? round=started.Context.RoundId;
            check(started is {RecognitionPath:"ordinary",IdentityLocked:false} && started.RemainingMapIds.Count==0,
                "S0 starts with ordinary discovery and no fabricated entered-index pool");

            var ordinary=await Take("batch02-event-000099","before-door");
            check(ordinary is {RecognitionPath:"ordinary",IdentityLocked:false,Context.MapId:null}
                && ordinary.RemainingMapIds.Count==0 && !runtime.TryReadRender(out _),
                "actual floor2 no-glyph entry preserves the ordinary unsupported-entry result");
            await Ask("close_map");
            capture.TransformPixels=(pixels,width,height,stride)=>
            {
                for(int y=(int)(height*.19);y<(int)(height*.61);y++)
                    for(int x=(int)(width*.84);x<(int)(width*.92);x++)
                    {int p=y*stride+x*4;pixels[p]=40;pixels[p+1]=32;pixels[p+2]=24;pixels[p+3]=255;}
            };
            var unknownFloor=await Take("batch02-event-000097","reliable-door-unknown-floor");
            check(unknownFloor.MapOpen && unknownFloor.Context.FloorId is null && unknownFloor.RecognitionPath=="structure-index"
                && unknownFloor.RemainingMapIds.Count==0 && !unknownFloor.NativeGeometryVerified && !runtime.TryReadRender(out _),
                "reliable door latches the round strategy before unknown-floor veto without fitting or creating a candidate ledger");
            capture.TransformPixels=null;await Ask("close_map");
            var noDoorAfterUnknown=await Take("batch02-event-000099","known-floor-after-door-no-longer-visible");
            check(noDoorAfterUnknown.RecognitionPath=="structure-index" && noDoorAfterUnknown.RemainingMapIds.Count==30
                && noDoorAfterUnknown.EliminatedMapIds.Count==0 && !noDoorAfterUnknown.IdentityLocked,
                "later readable floor without a door continues the previously entered strategy");
            await Ask("close_map");
            var entered=await Take("batch02-event-000097","door-entry");
            var independent=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(indexOracle,"batch02-event-000097-index.json")))!;
            var excluded=independent["contradictedMapIds"]!.AsArray().Select(row=>row!.GetValue<string>()).ToHashSet();
            var original=independent["evaluatedMapIds"]!.AsArray().Select(row=>row!.GetValue<string>()).ToHashSet();
            check(entered.RecognitionPath=="structure-index" && entered.EliminatedMapIds.ToHashSet().SetEquals(excluded)
                && entered.RemainingMapIds.ToHashSet().SetEquals(original.Except(excluded)),
                "real door entry narrows the default Runtime pool exactly as the independent positive-contradiction report");
            check(entered is {IdentityLocked:false,IdentityKind:"candidate",Context.MapId:null,Context.FloorId:"1"}
                && entered.ProvisionalMapId is not null && entered.RemainingMapIds.Contains(entered.ProvisionalMapId),
                "first usable map remains a provisional member rather than a unique identity");
            check(entered.Candidates.Select(row=>row.MapId).ToHashSet().SetEquals(entered.RemainingMapIds)
                && entered.Candidates.All(row=>!row.IdentityVerified),
                "unresolved surviving candidates remain selectable without fabricated identity evidence");
            check(runtime.TryReadRender(out var first),"actual entered-index route produces current-frame material");
            CheckMaterial(entered,first!,"door-entry");
            check(runtime.ReadMaterialToRemember() is null,"provisional material has no steady learning proposal");
            runtime.AcknowledgeRenderMaterial(first!);
            check(runtime.ReadMaterialToRemember() is null && !runtime.ReadSnapshot().LiveAuthorization,
                "render acknowledgement cannot promote a provisional pose to steady learning or native live authority");
            string[] remaining=entered.RemainingMapIds.ToArray();string? resource=entered.Context.ResourceId;

            await Ask("close_map");
            var sparse=await Take("batch02-event-000099","entered-sparse-floor2");
            CheckPool(sparse,remaining,"sparse target floor without a door");
            check(sparse.Context.FloorId=="2" && !sparse.IdentityLocked,
                "target floor changes without borrowing identity from the previous displayed floor");
            if(runtime.TryReadRender(out var sparseMaterial))CheckMaterial(sparse,sparseMaterial!,"sparse-floor2");
            else check(!sparse.NativeGeometryVerified && !sparse.Display.CanonicalVerified,
                "unavailable sparse-floor geometry leaves no previous-floor display authority");
            await Ask("close_map");
            var reopened=await Take("batch02-event-000097","same-filter-return-floor1");
            CheckPool(reopened,remaining,"A to B to A with one Runtime and resource owner");
            check(runtime.TryReadRender(out var fresh) && fresh!.Stamp.FrameId!=first!.Stamp.FrameId,
                "reopened material is newly authorized for this captured frame");
            CheckMaterial(reopened,fresh!,"returned-floor1");
            int calls=capture.Calls;
            await Ask("set_context",new {season="s0",mode="difficult",profileId="shared-structure",refreshDisplay=true});
            var refreshed=Save("same-profile-refresh");CheckPool(refreshed,remaining,"display preference refresh");
            check(refreshed.Context.ResourceId==resource && capture.Calls==calls,
                "same display refresh neither rebinds structural resources nor acquires another frame");

            string chosen=refreshed.ProvisionalMapId ?? throw new InvalidDataException("Verified preview missing.");
            var hold=capture.HoldNext(ignoreCancellation:true);var confirmation=Ask("confirm_route",new {mapId=chosen});
            try
            {
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));var pending=Save("manual-before-capture");
                check(pending.IdentityLocked && pending.Context.MapId==chosen && pending.IdentitySource=="manual"
                    && pending.WorkActive && !pending.NativeGeometryVerified && !runtime.TryReadRender(out _),
                    "human choice locks identity immediately before the blocked alignment capture");
                runtime.InvalidateCapture("map-closed");
            }
            finally{hold.Release.TrySetResult();}
            var late=await confirmation.WaitAsync(TimeSpan.FromSeconds(30));await Ask("close_map");var closed=Save("manual-late-capture");
            check(!late.Receipt.Success && !closed.MapOpen && closed.IdentityLocked && closed.Context.MapId==chosen
                && !runtime.TryReadRender(out _) && !closed.NativeGeometryVerified,
                "late cancelled capture cannot revoke human identity or resurrect closed-map material");
            CheckPool(closed,remaining,"late manual capture");
            await Ask("reselect_map");var reselected=Save("reselect");CheckPool(reselected,remaining,"same-round reselection");
            check(!reselected.IdentityLocked && reselected.Context.MapId is null,"reselection clears chosen identity without clearing the evidence ledger");
            var afterReselect=await Take("batch02-event-000099","reselect-sparse-floor2");
            CheckPool(afterReselect,remaining,"sparse capture after reselection");

            await Ask("end_round");await Ask("start_round");var next=Save("new-round");
            check(next.Context.RoundId!=round && next is {RecognitionPath:"ordinary",IdentityLocked:false}
                && next.RemainingMapIds.Count==0 && next.EliminatedMapIds.Count==0,
                "a new S0 round resets door entry and owns a fresh lazy ledger");
            var restarted=await Take("batch02-event-000097","next-round-door-entry");CheckPool(restarted,remaining,"new round independently reaches the same evidence result");
            await Ask("end_round");await Ask("start_round");
            capture.Select("batch02-event-000097",false);var lateHold=capture.HoldNext(ignoreCancellation:true);
            var delayed=Ask("capture_now");
            try{await lateHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));runtime.InvalidateCapture("map-closed");}
            finally{lateHold.Release.TrySetResult();}
            var obsolete=await delayed.WaitAsync(TimeSpan.FromSeconds(30));await Ask("close_map");var obsoleteState=Save("closed-before-first-door-capture");
            check(!obsolete.Receipt.Success && obsoleteState.RecognitionPath=="ordinary"
                && obsoleteState.RemainingMapIds.Count==0 && !runtime.TryReadRender(out _),
                "capture retired before acquisition cannot enter the index or publish material late");

            await Ask("end_round");await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});
            await Ask("start_round");var nightmare=await Take("batch01-event-000006","nightmare-unbound-marker-source");
            check(nightmare.RecognitionPath=="structure-index" && nightmare.RemainingMapIds.Count==26
                && nightmare.EliminatedMapIds.Count==0 && nightmare is {IdentityLocked:false,IdentityKind:"candidate",Context.MapId:null},
                "nightmare glyph enters the index but unavailable marker coordinates cannot invent exclusions or a registered family");
            if(runtime.TryReadRender(out var nightmareMaterial))CheckMaterial(nightmare,nightmareMaterial!,"nightmare");
            await Ask("end_round");await Ask("set_context",new {season="s1",mode="nightmare",profileId="shared-structure"});
            await Ask("start_round");var s1=Save("s1-resource-boundary");
            check(s1.RecognitionPath=="ordinary" && s1.RemainingMapIds.Count==15
                && s1.EliminatedMapIds.Count==0 && !s1.RemainingMapIds.Intersect(remaining).Any()
                && s1.Context.ResourceId!=resource,
                "S1 resource binding starts its own original pool without S0 evidence or provisional state");
        }
        finally{runtime.Shutdown();}
        await S0FloorPoseRegression.Run(resources,replay,output,check);
        await S0PreparationGateRegression.Run(resources,replay,output,check);

        Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        async Task<LiveRuntimeState> Take(string id,string label)
        {
            capture.Select(id,false);int calls=capture.Calls;var result=await Ask("capture_now");
            var state=Save(label,result);check(capture.Calls==calls+1,label+" consumes exactly one new recorded client capture");return state;
        }
        LiveRuntimeState Save(string label,RuntimeOperationResult? result=null)
        {
            var state=runtime.ReadState();bool available=runtime.TryReadRender(out var packet);
            evidence.Add(JsonSerializer.SerializeToNode(new {label,state,receipt=result?.Receipt,available,
                materialMap=packet?.MapId,stamp=packet?.Stamp,bridge=packet?.Material.Bridge}));
            File.WriteAllText(Path.Combine(output,"s0-runtime-sequence.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));return state;
        }
        void CheckPool(LiveRuntimeState state,IEnumerable<string> expected,string label)=>check(state.RecognitionPath=="structure-index"
            && state.RemainingMapIds.ToHashSet().SetEquals(expected),label+" retains entered strategy and only surviving candidates");
        void CheckMaterial(LiveRuntimeState state,RuntimeRenderPacket packet,string label)=>check(
            packet.MapId==(state.Context.MapId??state.ProvisionalMapId) && state.RemainingMapIds.Contains(packet.MapId)
            && packet.Stamp.FrameId==state.Context.FrameId && state.NativePose?.FloorId==state.Context.FloorId
            && state.NativePose?.ResourceId==state.Context.ResourceId && state.NativeGeometryVerified && state.Display.CanonicalVerified,
            label+" consumes a surviving member with fresh target-floor native pose and current-frame Core display authority");
    }
}
