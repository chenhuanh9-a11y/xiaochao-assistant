using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Settings;
using StarMap.Core.Session;
using StarMap.Runtime;
using StarMap.Vision;

internal static class ManualLearningRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        await File.WriteAllTextAsync(Path.Combine(output,"scope.txt"),"Recorded post-Nov-5 PNG capture port explicitly simulates LiveClient provenance only inside isolated test roots. Actual Runtime/vision/store/Session paths; storage error/barrier wrappers replace only the IO boundary. No current live capture, native input, game or adaptive recognition claim.");
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var originals=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        foreach(var row in originals)
        {
            string mode=row!["mode"]!.GetValue<string>(),map=row["mapId"]!.GetValue<string>(),kind=row["kind"]!.GetValue<string>();
            var data=new ExperimentalDataRoot(Path.Combine(output,row["name"]!.GetValue<string>()));
            var capture=new RecordedCapture(index){EvidenceKind=kind=="learn-recorded"?CaptureEvidenceKind.RecordedClient:CaptureEvidenceKind.LiveClient};
            var runtime=new LiveRuntime(capture,resources,new(){ConfirmationMemoryFactory=catalog=>new ManualConfirmationMemoryStore(data,catalog)});
            var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season="s0",mode,profileId="shared-structure"});await Ask("start_round");
                capture.Select(row["initial"]!.GetValue<string>(),false);await Ask("capture_now");
                if(kind=="learn-closed")await Ask("close_map");
                if(kind=="learn-unavailable")capture.FailNext();
                int before=capture.Calls;var result=await Ask("confirm_route",new {mapId=map,evidenceKind="live-client-capture"});
                check(result.Receipt.Success==row["result"]!["success"]!.GetValue<bool>() && capture.Calls==before+row["captureDelta"]!.GetValue<int>()
                    && runtime.ReadState().IdentityLocked,row["name"]+" actual confirmation preserves original pose/acquisition outcome");
                var state=JsonSerializer.SerializeToNode(runtime.ReadState(),new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
                await File.WriteAllTextAsync(Path.Combine(output,row["name"]+".json"),state.ToJsonString());
                check(state["manualLearning"] is JsonObject,mode+" confirmation exposes independent manual-learning diagnostics");
                var learning=state["manualLearning"]!;
                check(learning["status"]!.GetValue<string>()==row["learning"]!["status"]!.GetValue<string>()
                    && learning["saved"]!.GetValue<bool>()==row["learning"]!["saved"]!.GetValue<bool>(),row["name"]+" original independent learning status");
                if(row["learning"]!["reason"] is { } reason)check(learning["reason"]!.GetValue<string>()==reason.GetValue<string>(),row["name"]+" original skip reason");
                var reopened=new ManualConfirmationMemoryStore(data,EntryPassportCatalog.Load(resources,"s0",mode));
                var records=reopened.Records();check(records.Count==row["records"]!.AsArray().Count,row["name"]+" actual isolated JSONL round trip");
                if(records.Count>0)
                {
                    var actual=records[0];var expected=row["records"]![0]!;
                    foreach(string key in new[]{"schemaVersion","packId","mapId","mapName","floor","labelSource","state","runtimeDisplayEnabled",
                        "samplePath","sampleSha256","sampleSize","sampleEvidenceAccepted","clientBounds"})
                        check(JsonNode.DeepEquals(actual[key],expected[key]),row["name"]+" original persisted "+key);
                    string image=Path.Combine(reopened.Root,actual["samplePath"]!.GetValue<string>());
                    check(File.Exists(image) && Convert.ToHexStringLower(SHA256.HashData(await File.ReadAllBytesAsync(image)))==actual["sampleSha256"]!.GetValue<string>(),
                        row["name"]+" JPEG bytes independently match their original-format sample identity");
                    check(reopened.ResolvedRecords().Count==1 && reopened.ExemplarPaths(actual["floor"]!.GetValue<string>())[map].Count==1,row["name"]+" reopened legacy reader selects the sample");
                }
                if(kind=="learn-recorded")check(!Directory.Exists(data.Path),mode+" forged action evidence creates no sample or data directory");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        await Boundaries();
        StorageContracts();
        foreach(var (mode,sample,map) in new[]{("nightmare","batch02-event-000319","107012"),("difficult","batch02-event-000307","106008")})
        {
            var capture=new RecordedCapture(index){EvidenceKind=CaptureEvidenceKind.LiveClient};capture.Select(sample,false);
            int factoryCalls=0;var runtime=new LiveRuntime(capture,resources,new(){ConfirmationMemoryFactory=_=>{factoryCalls++;throw new IOException("S1 must not construct a memory store");}});
            var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season="s1",mode,profileId="shared-structure"});await Ask("prepare_manual_alignment");
                await Ask("confirm_route",new {mapId=map});
                check(factoryCalls==0 && runtime.ReadState().ManualLearning is {Status:"skipped",Reason:"season-manual-learning-unregistered",Saved:false},
                    mode+" S1 never constructs the S0 label store even with simulated live provenance");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }

        async Task Boundaries()
        {
            foreach(string boundary in new[]{"before-acceptance","after-acceptance","write-error","reload-error","locked-log"})
            {
                var data=new ExperimentalDataRoot(Path.Combine(output,boundary));
                var capture=new RecordedCapture(index){EvidenceKind=CaptureEvidenceKind.LiveClient};capture.Select("batch01-event-000006",false);
                var memory=new BoundaryMemory(new ManualConfirmationMemoryStore(data,EntryPassportCatalog.Load(resources,"s0","nightmare")),boundary);
                var runtime=new LiveRuntime(capture,resources,new(){ConfirmationMemoryFactory=_=>memory});var sequence=new SessionSequencer();
                FileStream? locked=null;
                try
                {
                    await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});await Ask("start_round");await Ask("capture_now");
                    if(boundary=="locked-log")
                    {Directory.CreateDirectory(memory.Inner.Root);locked=new(memory.Inner.LogPath,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);}
                    RecordedCapture.Hold? hold=boundary=="before-acceptance"?capture.HoldNext():null;
                    // Match the Session worker's background invocation; direct
                    // synchronous completion would block this test at its own IO barrier.
                    var operation=Task.Run(()=>Ask("confirm_route",new {mapId="nightmare-12"}));
                    if(hold is not null)
                    {
                        await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));runtime.Shutdown();hold.Release.TrySetResult();await operation;
                        check(memory.Calls==0 && !Directory.Exists(data.Path),"close before label acceptance cannot persist a captured successor");
                    }
                    else if(boundary=="after-acceptance")
                    {
                        try
                        {
                            await memory.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                            check(runtime.ReadState().IdentityLocked && runtime.TryReadRender(out _),"accepted disk write does not hold the runtime state lock");
                            runtime.Shutdown();
                        }
                        finally{memory.Release.TrySetResult();}
                        await operation;
                        check(memory.Inner.Records().Count==1 && runtime.ReadState() is {Closed:true,MapOpen:false,ManualLearning:null} && !runtime.TryReadRender(out _),
                            "accepted immutable label drains after shutdown without restoring the old scene or round");
                    }
                    else
                    {
                        var result=await operation;var learning=runtime.ReadState().ManualLearning;
                        locked?.Dispose();locked=null;
                        check(result.Receipt.Success && runtime.TryReadRender(out _) && learning is {Status:"error",Reloaded:false}
                            && learning.Saved==(boundary=="reload-error"),boundary+" preserves actual pose success and reports partial persistence separately");
                        check(memory.Inner.Records().Count==(boundary=="reload-error"?1:0),boundary+" independently read actual saved labels");
                        if(boundary=="locked-log")check(Directory.EnumerateFiles(memory.Inner.Root,"*.jpg",SearchOption.AllDirectories).Count()==1,
                            "real locked log preserves the partial JPEG evidence without claiming the label append succeeded");
                        await Ask("capture_now");check(memory.Calls==1,boundary+" automatic observation never retries a human-label write");
                    }
                }
                finally{locked?.Dispose();memory.Release.TrySetResult();runtime.Shutdown();}
                Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                    JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
            }
        }

        void StorageContracts()
        {
            var catalog=EntryPassportCatalog.Load(resources,"s0","nightmare");
            using var pixels=Cv2.ImDecode(File.ReadAllBytes(index["batch01-event-000006"]["image"]!.GetValue<string>()),ImreadModes.Color);
            var store=new ManualConfirmationMemoryStore(new(Path.Combine(output,"store-resolution")),catalog);
            int[] bounds=[0,0,pixels.Width,pixels.Height];
            store.RecordConfirmation("nightmare-12","1",pixels,bounds);store.RecordConfirmation("nightmare-12","1",pixels,bounds);
            check(store.Records().Count==2 && store.ResolvedRecords().Count==1 && store.ExemplarPaths("1")["nightmare-12"].Count==1,
                "duplicate JPEG labels retain the audit and select only the newest consistent exemplar");
            string other=catalog.Maps.First(map=>map.MapId!="nightmare-12").MapId;store.RecordConfirmation(other,"1",pixels,bounds);
            check(store.Records().Count==3 && store.ResolvedRecords().Count==0 && store.ExemplarPaths("1").Values.All(paths=>paths.Count==0),
                "contradictory labels quarantine the whole JPEG identity without deleting audit rows");
            using(var blank=new Mat(450,800,MatType.CV_8UC3,Scalar.Black))store.RecordConfirmation("nightmare-12","1",blank,bounds);
            check(store.Records().Count==4 && store.Records()[3] is var rejected && rejected["sampleEvidenceAccepted"]!.GetValue<bool>()==false
                && rejected["samplePath"] is null,"rejected sample retains a label-only original-format row");
            File.AppendAllText(store.LogPath,"{broken-json\n");
            check(store.Records().Count==4,"malformed JSONL tail is ignored without rewriting old labels");
            var escaping=store.Records()[0].DeepClone().AsObject();escaping["samplePath"]="../../outside.jpg";escaping["sampleSha256"]="different";
            File.AppendAllText(store.LogPath,escaping.ToJsonString()+"\n");bool refused=false;
            try{store.ExemplarPaths("1");}catch(IOException){refused=true;}
            check(refused,"imported sample paths cannot read or prune outside the explicitly selected memory root");
            var retained=new ManualConfirmationMemoryStore(new(Path.Combine(output,"store-retention")),catalog);
            for(int i=0;i<16;i++)
            {
                using var varied=pixels.Clone();Cv2.Rectangle(varied,new Rect(pixels.Width/2,pixels.Height/2,20,20),new Scalar(i*15,75,135),-1);
                retained.RecordConfirmation("nightmare-12","1",varied,bounds);
            }
            check(retained.Records().Select(row=>row["sampleSha256"]!.GetValue<string>()).Distinct().Count()==16,
                "retention fixture changes sixteen distinct JPEGs inside the original crop");
            check(retained.Records().Count==16 && retained.ExemplarPaths("1")["nightmare-12"].Count==6
                && Directory.EnumerateFiles(Path.Combine(retained.Root,"samples","nightmare-12","1"),"*.jpg").Count()==12,
                "retention preserves all sixteen labels and the six usable exemplars within twelve recent owned JPEGs");
        }
    }

    sealed class BoundaryMemory(ManualConfirmationMemoryStore inner,string boundary):IManualConfirmationMemory
    {
        internal ManualConfirmationMemoryStore Inner=>inner;
        internal int Calls;
        internal TaskCompletionSource Started=new(TaskCreationOptions.RunContinuationsAsynchronously),Release=new(TaskCreationOptions.RunContinuationsAsynchronously);
        public JsonObject RecordConfirmation(string map,string floor,Mat frame,int[] bounds)
        {
            Calls++;
            if(boundary=="write-error")throw new IOException("isolated label-write failure");
            if(boundary=="after-acceptance"){Started.TrySetResult();if(!Release.Task.Wait(TimeSpan.FromSeconds(15)))throw new TimeoutException("isolated IO barrier was not released");}
            return inner.RecordConfirmation(map,floor,frame,bounds);
        }
        public void Reload(){if(boundary=="reload-error")throw new IOException("isolated reload failure");inner.Reload();}
    }
}
