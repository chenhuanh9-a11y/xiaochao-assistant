using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class S1FloorObserverRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var rows=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray();
        var index=rows.ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var originals=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        var evidence=new JsonArray();
        foreach(var (mode,sample,map) in new[]{("nightmare","batch02-event-000319","107012"),("difficult","batch02-event-000307","106008")})
        {
            var capture=new RecordedCapture(index);var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season="s1",mode,profileId="shared-structure"});await Ask("start_round");
                capture.Select(sample,false);await Ask("capture_now");runtime.TryReadRender(out var initial);
                check(initial is not null,mode+" real recorded candidate reaches the canonical material consumer");
                int calls=capture.Calls;var same=await Ask("observe_candidate_floor");runtime.TryReadRender(out var unchanged);
                check(same.Receipt.Success && same.Receipt.Reason=="candidate-floor-current",mode+" dedicated candidate observer is dispatched");
                check(capture.Calls==calls+1 && ReferenceEquals(initial,unchanged),mode+" same-floor probe retains exact old packet without renewing pose or frame");
                CompareOriginal("observe_candidate_floor",sample,same);
                check(runtime.ReadSnapshot().AvailableCommands.Contains("observe_candidate_floor"),mode+" actual snapshot advertises candidate observer");
                if(mode=="nightmare")
                {
                    var pool=runtime.ReadState().RemainingMapIds.ToHashSet();capture.Select("batch02-event-000323",false);calls=capture.Calls;
                    var changed=await Ask("observe_candidate_floor");CompareOriginal("observe_candidate_floor","batch02-event-000323",changed);
                    check(capture.Calls==calls+2 && runtime.ReadState().Context.FloorId=="-2" && runtime.TryReadRender(out _),
                        "candidate layer change probes then captures fresh pixels for the real solve");
                    check(runtime.ReadState().RemainingMapIds.All(pool.Contains),"passive cross-floor observation cannot expand the round pool");
                    capture.Select("batch02-event-000400",false);calls=capture.Calls;var unknown=await Ask("observe_candidate_floor");
                    check(!unknown.Receipt.Success && unknown.Receipt.Reason=="candidate-floor-unavailable" && capture.Calls==calls+1
                        && runtime.ReadState().MapOpen && runtime.ReadState().ObservedFloor is null && !runtime.TryReadRender(out _),
                        "actual unknown-highlight PNG withdraws candidate pixels without closing the opening");
                    CompareOriginal("observe_candidate_floor","batch02-event-000400",unknown);
                }
                capture.Select(sample,false);
                await Ask("end_round");calls=capture.Calls;await Ask("prepare_manual_alignment");
                check(capture.Calls==calls && runtime.ReadState() is {RoundActive:true,ManualAlignmentSession:true,Context.MapId:null,Context.FloorId:null,ObservedFloor:null},
                    mode+" explicit manual preparation creates a separate empty session without capturing or seeding a floor");
                var missingMap=await Ask("fit_manual_frame");
                check(!missingMap.Receipt.Success && missingMap.Receipt.Reason=="manual-map-required" && capture.Calls==calls,
                    mode+" manual fit requires a selected identity before acquiring pixels");
                await Ask("confirm_route",new {mapId=map});
                check(capture.Calls==calls+1 && runtime.ReadState() is {ManualAlignmentSession:true,MapOpen:true,Context.FloorId:"-1"},
                    mode+" first manual confirmation captures and observes the actual floor without a previous frame");
                check(runtime.ReadState().IdentityLocked && runtime.ReadState().IdentitySource=="manual",mode+" explicit identity is selected before passive fitting");
                check(runtime.ReadSnapshot().AvailableCommands.Contains("observe_manual_floor")
                    && !runtime.ReadSnapshot().AvailableCommands.Contains("observe_candidate_floor"),mode+" explicit manual session owns its dedicated observer command");
                calls=capture.Calls;var malformed=await Ask("fit_manual_frame",new {inputObservationSequence="invalid"});
                check(!malformed.Receipt.Success && malformed.Receipt.Reason=="map-input-sequence-expired" && capture.Calls==calls,
                    mode+" malformed manual input observation is rejected before acquisition");
                calls=capture.Calls;var expired=await Ask("observe_manual_floor",new {inputObservationSequence=-1});
                check(!expired.Receipt.Success && expired.Receipt.Reason=="map-input-sequence-expired" && capture.Calls==calls,
                    mode+" stale physical input sequence cannot launch a manual observer capture");
                calls=capture.Calls;
                var manual=await Ask("observe_manual_floor");runtime.TryReadRender(out var fresh);
                check(manual.Receipt.Success && capture.Calls==calls+1 && fresh is not null && fresh.Stamp!=initial!.Stamp,
                    mode+" manual observer publishes a newly fitted current-frame packet using one capture");
                check(runtime.ReadState().Context.MapId==map && runtime.ReadState().ObservedFloor=="-1",mode+" fresh floor does not change the chosen map");
                CompareOriginal("observe_manual_floor",sample,manual);
                calls=capture.Calls;var renewed=await Ask("observe_manual_floor");runtime.TryReadRender(out var renewal);
                check(renewed.Receipt.Success && renewed.Receipt.Reason=="current-pose-refreshed" && capture.Calls==calls+1
                    && renewal is not null && renewal.Stamp!=fresh!.Stamp && ReferenceEquals(renewal.Material.Pixels,fresh.Material.Pixels),
                    mode+" selected-map renewal measures fresh full-client residual and retains source pixels");
                CompareOriginal("observe_manual_floor",sample,renewed);
                foreach(var (action,throws) in new[]{("fit_manual_frame",true),("observe_manual_floor",true),("fit_manual_frame",false),("observe_manual_floor",false)})
                {
                    calls=capture.Calls;var retained=runtime.ReadState().RemainingMapIds.ToArray();
                    if(throws)capture.ThrowNext(new IOException("recorded manual capture error"));else capture.FailNext();
                    string failureKind=throws?"exception":"normalized null-frame";
                    var failed=await Ask(action);
                    check(!failed.Receipt.Success && capture.Calls==calls+1
                        && runtime.ReadState() is {ManualAlignmentSession:true,MapOpen:true,Context.FloorId:null,ObservedFloor:null}
                        && runtime.ReadState().Context.MapId==map && runtime.ReadState().RemainingMapIds.SequenceEqual(retained)
                        && !runtime.TryReadRender(out _) && runtime.ReadSnapshot().AvailableCommands.Contains("observe_manual_floor"),
                        mode+" "+action+" capture "+failureKind+" retires current floor and pixels without closing the observed cycle");
                    var retried=await Ask("observe_manual_floor");
                    check(retried.Receipt.Success && capture.Calls==calls+2 && runtime.TryReadRender(out _),
                        mode+" passive observer recovers after "+action+" "+failureKind+" without another physical G edge");
                }
                if(mode=="nightmare")
                {
                    capture.Select("batch02-event-000400",false);calls=capture.Calls;
                    var unknown=await Ask("observe_manual_floor");
                    check(!unknown.Receipt.Success && unknown.Receipt.Reason=="s1-current-floor-unavailable" && capture.Calls==calls+1
                        && runtime.ReadState().MapOpen && runtime.ReadState().Context.FloorId is null && !runtime.TryReadRender(out _)
                        && runtime.ReadState().Context.MapId==map,"manual unknown read retains identity but no inherited floor or pose");
                    CompareOriginal("observe_manual_floor","batch02-event-000400",unknown);
                    await Ask("set_floor",new {floor="-3"});
                    check(runtime.ReadState().ManualFloor=="-3" && runtime.ReadState().Context.FloorId is null,
                        "an operator fallback cannot fill an unknown current S1 observation");
                    capture.Select(sample,false);await Ask("observe_manual_floor");
                    check(runtime.ReadState().ManualFloor is null && runtime.ReadState().Context.FloorId=="-1",
                        "first eligible S1 highlight immediately retires a manual fallback without S0 two-second hold");
                    capture.Select("batch02-event-000323",false);calls=capture.Calls;
                    var changed=await Ask("observe_manual_floor");runtime.TryReadRender(out var otherFloor);
                    CompareOriginal("observe_manual_floor","batch02-event-000323",changed);
                    check(capture.Calls==calls+1 && runtime.ReadState().Context.FloorId=="-2" && otherFloor is not null,
                        "sparse C1 -2 uses the target floor's own actual solve");
                    check(ReferenceEquals(fresh!.Scene?.Sources,otherFloor!.Scene?.Sources),"floor change retains resident scene sources");
                }
                var hold=capture.HoldNext();var operation=Ask("observe_manual_floor");
                try
                {
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));calls=capture.Calls;
                    var coalesced=await Ask("observe_manual_floor");
                    check(coalesced.Receipt.Reason=="floor-observation-coalesced" && capture.Calls==calls,
                        mode+" duplicate passive operation does not queue another capture");
                    runtime.InvalidateCapture("observer-context-invalidated");
                }
                finally{hold.Release.TrySetResult();}
                var obsolete=await operation;
                check(!obsolete.Receipt.Success && !runtime.TryReadRender(out _) && !runtime.ReadState().MapOpen,
                    mode+" delayed observer cannot restore pixels after context invalidation");
                capture.Select(sample,false);await Ask("capture_now");var remaining=runtime.ReadState().RemainingMapIds.ToArray();
                capture.Select("batch01-event-000002",false);calls=capture.Calls;
                var closed=await Ask("observe_manual_floor");
                check(!closed.Receipt.Success && closed.Receipt.Reason=="s1-map-closed" && capture.Calls==calls+1
                    && !runtime.ReadState().MapOpen && !runtime.TryReadRender(out _),mode+" one explicit S1 negative closes and withdraws current material");
                check(runtime.ReadState().Context.MapId==map && runtime.ReadState().RemainingMapIds.SequenceEqual(remaining),
                    mode+" closed observer preserves identity and the round's candidate constraints");
                CompareOriginal("observe_manual_floor","batch01-event-000002",closed);
                calls=capture.Calls;await Ask("observe_manual_floor");
                check(capture.Calls==calls,mode+" inactive observer cannot create a new opening");
                capture.Select(sample,false);var explicitFit=await Ask("fit_manual_frame");
                check(explicitFit.Receipt.Success && capture.Calls==calls+1 && runtime.ReadState() is {MapOpen:true,ManualAlignmentSession:true}
                    && runtime.TryReadRender(out _),mode+" explicit manual fit can acquire a fresh opening after passive closure");
                capture.Select("batch01-event-000002",false);await Ask("observe_manual_floor");
                await Ask("map",new {inputSequence=1,openIntent=true});
                var recovery=runtime.ReadState().MapOpenRecovery;
                check(recovery.Pending && recovery.Id is not null && runtime.ReadSnapshot().AvailableCommands.Contains("observe_manual_floor"),
                    mode+" only a fresh explicit opening arms the bounded manual-observer recovery");
                calls=capture.Calls;await Ask("observe_manual_floor",new {recoveryId="not-the-owned-recovery"});
                check(capture.Calls==calls && runtime.ReadState().MapOpenRecovery.Id==recovery.Id,
                    mode+" incorrect recovery id cannot acquire pixels or replace the pending recovery");
                await Task.Delay(210);capture.Select(sample,false);calls=capture.Calls;
                var recovered=await Ask("observe_manual_floor",new {recoveryId=recovery.Id});
                check(recovered.Receipt.Success && capture.Calls==calls+1 && runtime.ReadState().MapOpen
                    && !runtime.ReadState().MapOpenRecovery.Pending && runtime.TryReadRender(out _) && runtime.ReadState().Context.MapId==map,
                    mode+" dedicated manual observer consumes the owned due recovery using one actual capture and solve");
                await Ask("end_round");await Ask("start_round");capture.Select(sample,false);await Ask("capture_now");
                check(!runtime.ReadState().ManualAlignmentSession,mode+" new ordinary round retires the independent manual session policy");
                // Synthetic resize of recorded pixels exercises raw UNKNOWN;
                // this derived frame is not a natural screenshot or live proof.
                var retainedPool=runtime.ReadState().RemainingMapIds.ToArray();
                var retainedCandidates=runtime.ReadState().Candidates.Select(c=>c.MapId).ToArray();
                string smallId=mode+"-derived-small-client",smallPath=Path.Combine(output,smallId+".png");
                using(var image=Cv2.ImDecode(File.ReadAllBytes(index[sample]["image"]!.GetValue<string>()),ImreadModes.Color))
                using(var small=new Mat())
                {Cv2.Resize(image,small,new Size(800,450));Cv2.ImEncode(".png",small,out var encoded);File.WriteAllBytes(smallPath,encoded);}
                var resized=(JsonObject)index[sample].DeepClone();resized["id"]=smallId;resized["image"]=smallPath;
                resized["physicalClient"]!["Width"]=800;resized["physicalClient"]!["Height"]=450;
                index.Add(smallId,resized);capture.Select(smallId,false);
                var unknownCanvas=await Ask("observe_candidate_floor");
                check(!unknownCanvas.Receipt.Success && unknownCanvas.Receipt.Reason=="candidate-floor-unavailable" && !runtime.TryReadRender(out _),
                    mode+" unsupported-size raw mapOpen null withdraws pixels without confirming map close");
                capture.Select(sample,false);
                check(runtime.ReadState().MapOpen && runtime.ReadState().RemainingMapIds.SequenceEqual(retainedPool)
                    && runtime.ReadState().Candidates.Select(c=>c.MapId).SequenceEqual(retainedCandidates),
                    mode+" returning capture geometry reveals retained opening, pool and candidates without a new G edge");
                calls=capture.Calls;var resumed=await Ask("observe_candidate_floor");
                check(resumed.Receipt.Success && capture.Calls==calls+2 && runtime.ReadState().MapOpen && runtime.TryReadRender(out _),
                    mode+" next passive observer restores current geometry after raw unknown without reopening");
                evidence.Add(JsonSerializer.SerializeToNode(new {mode,state=runtime.ReadState(),capture.Calls}));
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
            void CompareOriginal(string action,string input,RuntimeOperationResult actual)
            {
                var source=originals.Single(c=>c!["mode"]!.GetValue<string>()==mode)!["steps"]!.AsArray()
                    .Last(s=>s!["action"]!.GetValue<string>()==action && s["input"]?.GetValue<string>()==input)!;
                var state=runtime.ReadState();bool rendered=runtime.TryReadRender(out var packet);
                evidence.Add(JsonSerializer.SerializeToNode(new {mode,action,input,state,actual.Receipt,
                    canonical=packet?.Material.Bridge.CanonicalToClient,materialMap=packet?.MapId,original=source}));
                File.WriteAllText(Path.Combine(output,"s1-floor-observer-comparison.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
                string? Normalize(JsonNode? n)=>string.IsNullOrEmpty(n?.ToString())?null:n.ToString();
                check(actual.Receipt.Success==source["result"]!["success"]!.GetValue<bool>() && state.MapOpen==source["mapOpen"]!.GetValue<bool>()
                    && state.Context.FloorId==Normalize(source["floor"]) && state.ObservedFloor==Normalize(source["observedFloor"])
                    && rendered==(source["render"] is not null),mode+"/"+action+"/"+input+" matches original observed floor, open, verdict and pixel availability");
                if(rendered)
                {
                    // The original preview has no cumulative round pool and
                    // can select another sparse-floor candidate. Compare pose
                    // only with its independently solved identical resource.
                    var expected=originals.Single(c=>c!["mode"]!.GetValue<string>()==mode)!["steps"]!.AsArray()
                        .Last(s=>s!["input"]?.GetValue<string>()==input && s["render"]?["mapId"]?.GetValue<string>()==packet!.MapId)!
                        ["render"]!["canonicalMatrix"]!;
                    var wanted=expected.Deserialize<double[][]>()!;
                    check(packet!.Material.Bridge.CanonicalToClient is { } matrix && matrix.ToArray().SelectMany(r=>r)
                        .Zip(wanted.SelectMany(r=>r)).All(p=>Math.Abs(p.First-p.Second)<=1e-5),
                        mode+"/"+action+"/"+input+" final canonical transform matches original real-resource solver");
                }
                check(!runtime.ReadSnapshot().LiveAuthorization,mode+" recorded observer never becomes live authorization");
            }
        }
        await File.WriteAllTextAsync(Path.Combine(output,"s1-floor-observer.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
    }
}
