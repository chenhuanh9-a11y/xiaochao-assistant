using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;
using StarMap.Vision;

internal static class ManualStorageSafetyRegression
{
    internal static async Task Run(string resources,string replay,string ancestorAlias,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var catalog=EntryPassportCatalog.Load(resources,"s0","nightmare");
        using var pixels=Cv2.ImDecode(File.ReadAllBytes(index["batch01-event-000006"]["image"]!.GetValue<string>()),ImreadModes.Color);
        int[] bounds=[0,0,pixels.Width,pixels.Height];var results=new JsonArray();
        var tail=Store("partial-tail");tail.RecordConfirmation("nightmare-12","1",pixels,bounds);
        File.AppendAllText(tail.LogPath,"{\"mapId\":");tail.RecordConfirmation("nightmare-12","1",pixels,bounds);
        Add("partial-tail",tail.Records().Count==2,new {rows=tail.Records().Count});

        var data=new ExperimentalDataRoot(Path.Combine(output,"post-save"));var post=new ManualConfirmationMemoryStore(data,catalog);
        var bad=post.RecordConfirmation("nightmare-12","1",pixels,bounds).DeepClone().AsObject();bad["samplePath"]="../../outside.jpg";bad["sampleSha256"]=new string('a',64);
        File.AppendAllText(post.LogPath,bad.ToJsonString()+"\n");
        var capture=new RecordedCapture(index){EvidenceKind=CaptureEvidenceKind.LiveClient};capture.Select("batch01-event-000006",false);
        var runtime=new LiveRuntime(capture,resources,new(){ConfirmationMemoryFactory=_=>post});var sequence=new SessionSequencer();
        try
        {
            await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});await Ask("start_round");await Ask("capture_now");
            await Ask("confirm_route",new {mapId="nightmare-12"});
            var learning=runtime.ReadState().ManualLearning;
            Add("post-save-failure",learning is {Status:"error",Saved:true} && post.Records().Count==3 && runtime.TryReadRender(out _),
                new {learning,rows=post.Records().Count});
        }
        finally{runtime.Shutdown();}

        var ownership=Store("ownership");
        for(int i=0;i<16;i++)RecordVariation(ownership,i);
        string directory=Path.Combine(ownership.Root,"samples","nightmare-12","1");
        string unknown=Path.Combine(directory,"00000000000000000000.jpg");File.WriteAllText(unknown,"unregistered JPEG-shaped file");File.SetLastWriteTimeUtc(unknown,new(2000,1,1));
        string edited=Directory.EnumerateFiles(directory,"*.jpg").Where(path=>path!=unknown).OrderBy(File.GetLastWriteTimeUtc).First();
        File.WriteAllText(edited,"user edited bytes");File.SetLastWriteTimeUtc(edited,new(1999,1,1));
        RecordVariation(ownership,16);
        bool unknownRetained=File.Exists(unknown),editedRetained=File.Exists(edited);
        Add("ownership",unknownRetained && editedRetained && File.ReadAllText(unknown)=="unregistered JPEG-shaped file" && File.ReadAllText(edited)=="user edited bytes",
            new {unknownRetained,editedRetained});

        if(!OperatingSystem.IsWindows() || (new DirectoryInfo(ancestorAlias).Attributes&FileAttributes.ReparsePoint)==0)
            throw new InvalidOperationException("A precreated isolated Windows junction is required for the ancestor-link control.");
        var linked=new ManualConfirmationMemoryStore(new(Path.Combine(ancestorAlias,"nested","isolated")),catalog);
        bool refused=false;try{linked.RecordConfirmation("nightmare-12","1",pixels,bounds);}catch(IOException){refused=true;}
        int files=Directory.EnumerateFiles(ancestorAlias,"*",SearchOption.AllDirectories).Count();
        Add("ancestor-link",refused && files==0,new {refused,files});
        await File.WriteAllTextAsync(Path.Combine(output,"storage-safety-cases.json"),results.ToJsonString(new(){WriteIndented=true}));
        check(results.All(row=>row!["passed"]!.GetValue<bool>()),"all four actual storage recovery/ownership/link cases pass");

        ManualConfirmationMemoryStore Store(string name)=>new(new(Path.Combine(output,name)),catalog);
        void RecordVariation(ManualConfirmationMemoryStore store,int i)
        {using var varied=pixels.Clone();Cv2.Rectangle(varied,new Rect(pixels.Width/2,pixels.Height/2,20,20),new Scalar(i*15,75,135),-1);store.RecordConfirmation("nightmare-12","1",varied,bounds);}
        void Add(string name,bool passed,object detail)=>results.Add(new JsonObject { ["name"]=name,["passed"]=passed,["detail"]=JsonSerializer.SerializeToNode(detail)});
        Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
    }
}
