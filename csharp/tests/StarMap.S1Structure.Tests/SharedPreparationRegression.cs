using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;

internal static class SharedPreparationRegression
{
    static readonly JsonSerializerOptions Json=new(){WriteIndented=true,NumberHandling=JsonNumberHandling.AllowNamedFloatingPointLiterals};
    internal static int Run(string resources,string replay,string oracle,string output)
    {
        if(Directory.Exists(output))throw new IOException("Preserve existing evidence; supply a new output.");
        Directory.CreateDirectory(output);var checks=new List<object>();var pairs=new List<object>();int exit=0;
        try
        {
            foreach(string kind in new[]{"equal-area-label-order","low-chain-primary","over32-islands","invalid-largest-does-not-skip"})
            {
                using var control=Control(kind);Primary(control,kind);
            }
            var rows=JsonNode.Parse(File.ReadAllBytes(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
                .ToDictionary(n=>n!["id"]!.GetValue<string>(),n=>n!.AsObject());
            string assets=JsonNode.Parse(File.ReadAllBytes(oracle))!["cases"]![0]!["assetRoot"]!.GetValue<string>();
            var catalog=ResourceCatalog.Load(resources);
            using var oldPipeline=new SharedStructureFitPipeline(catalog);using var sharedPipeline=new SharedStructureFitPipeline(catalog);
            var filters=new Dictionary<string,(StructureCatalog Catalog,StructureIndexFilter Old,StructureIndexFilter Shared)>();
            var usedPools=new HashSet<(string Mode,string Floor)>();int caseOrder=0;
            try
            {
                foreach(int number in new[]{249,255,257,307,319,323})
                {
                    int ordinal=caseOrder++;
                    string id="batch02-event-"+number.ToString("D6");var row=rows[id];string mode=row["mode"]!.GetValue<string>();
                    Check(DateTimeOffset.Parse(row["capturedAt"]!.GetValue<string>())>=new DateTimeOffset(2026,11,5,0,0,0,TimeSpan.Zero),id+" original capture is current");
                    using var frame=Cv2.ImDecode(File.ReadAllBytes(row["image"]!.GetValue<string>()),ImreadModes.Color);
                    Primary(frame,id);
                    var observation=FloorObservation.Observe(frame,"s1",mode,s1Use:S1FloorUse.StructureCandidate);
                    Check(observation.MapOpen==true && observation.FloorId is not null,id+" actual floor observation admits both comparison paths");
                    string floor=observation.FloorId!;
                    bool firstPoolUse=usedPools.Add((mode,floor));
                    if(!filters.TryGetValue(mode,out var group))
                    {var native=StructureCatalog.FromResources(catalog,mode);group=(native,new(catalog,native,assets),new(catalog,native,assets));filters.Add(mode,group);}
                    for(int repeat=0;repeat<3;repeat++)
                    {
                        Result old,shared;
                        if((repeat+ordinal)%2==0){old=Evaluate(false);shared=Evaluate(true);}else{shared=Evaluate(true);old=Evaluate(false);}
                        File.WriteAllText(Path.Combine(output,id+"-"+repeat+"-old.json"),JsonSerializer.Serialize(old,Json));
                        File.WriteAllText(Path.Combine(output,id+"-"+repeat+"-shared.json"),JsonSerializer.Serialize(shared,Json));
                        Check(JsonNode.DeepEquals(Canonical(old.Index),Canonical(shared.Index)),id+"/"+repeat+" complete index evidence agrees with unchanged separate preparation");
                        Check(JsonNode.DeepEquals(Canonical(old.Scan),Canonical(shared.Scan)),id+"/"+repeat+" full candidate fits, matrices, residuals, refusals and order are unchanged");
                        Check(shared.Preparation is {IslandPasses:1},id+"/"+repeat+" index and complete scanner consume one original component pass");
                        pairs.Add(new{id,repeat,order=(repeat+ordinal)%2==0?"old-first":"shared-first",poolState=firstPoolUse && repeat==0?"first-pool-use":"warm-pool",
                            oldMs=old.ElapsedMs,sharedMs=shared.ElapsedMs,shared.Preparation,
                            scope="Globally balanced order; exhaustive recorded index/catalog-scan work, not production first-material latency. Pool cold/warm only, not process/GPU classification."});
                        File.WriteAllText(Path.Combine(output,"pairs.json"),JsonSerializer.Serialize(pairs,Json));
                    }
                    Result Evaluate(bool share)
                    {
                        var clock=Stopwatch.StartNew();JsonObject index,scan;StructureFramePreparationStats? stats=null;
                        if(share)
                        {
                            using var prepared=new StructureFramePreparation(frame);
                            var gate=group.Shared.DetectAnchor(prepared);
                            index=group.Shared.Evaluate(prepared,group.Catalog.MapIds,floor,gate);
                            scan=sharedPipeline.CreateScanner(group.Catalog).Scan(prepared,new(floor,"current-frame-verified"));
                            stats=prepared.Statistics;
                        }
                        else
                        {
                            var gate=group.Old.DetectAnchor(frame);index=group.Old.Evaluate(frame,group.Catalog.MapIds,floor,gate);
                            scan=oldPipeline.CreateScanner(group.Catalog).Scan(frame,new(floor,"current-frame-verified"));
                        }
                        return new(index,scan,clock.Elapsed.TotalMilliseconds,stats);
                    }
                }
                using var sample=Cv2.ImDecode(File.ReadAllBytes(rows["batch02-event-000319"]["image"]!.GetValue<string>()),ImreadModes.Color);
                var nm=filters["nightmare"];var scanner=sharedPipeline.CreateScanner(nm.Catalog);
                using var owned=new StructureFramePreparation(sample);
                var before=scanner.Scan(owned,new("-1","current-frame-verified"));
                sample.SetTo(Scalar.All(0));
                var after=scanner.Scan(owned,new("-1","current-frame-verified"));
                Check(JsonNode.DeepEquals(Canonical(before),Canonical(after)),"mutating the caller buffer cannot change the owned frame or its candidate evidence");
                var cancelled=scanner.Scan(owned,new("-1","current-frame-verified",Cancelled:()=>true));
                Check(cancelled["status"]!.GetValue<string>()=="cancelled" && cancelled["poses"]!.AsObject().Count==0,"cancelled shared scan cannot return old supported poses");
                owned.Dispose();bool rejected=false;try{scanner.Scan(owned,new("-1"));}catch(ObjectDisposedException){rejected=true;}
                Check(rejected,"disposed operation owner cannot be used for another scan");
            }
            finally{foreach(var group in filters.Values){group.Old.Dispose();group.Shared.Dispose();}}
        }
        catch(Exception error){exit=1;File.WriteAllText(Path.Combine(output,"error.txt"),error.ToString());Console.Error.WriteLine(error);}
        File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(new{exit,checks,pairs,
            scope="Actual legacy Mat and new shared-owner production paths. Original pixels and synthetic primary-choice controls; no expected pose injected, no native/game/latency claim."},Json));
        return exit;

        void Check(bool ok,string name){checks.Add(new{name,passed=ok});Console.WriteLine((ok?"ok ":"FAIL ")+name);if(!ok)throw new InvalidDataException(name);}
        void Primary(Mat original,string name)
        {
            using var old=Geometry.Current(original,new Rect(0,0,original.Cols,original.Rows));
            using var owned=new StructureFramePreparation(original);
            var flags=BindingFlags.Instance|BindingFlags.NonPublic;
            var current=(Observation?)typeof(StructureFramePreparation).GetMethod("Primary",flags)!.Invoke(owned,null);
            var frame=(Mat)typeof(StructureFramePreparation).GetProperty("Frame",flags)!.GetValue(owned)!;
            Check((old is null)==(current is null),name+" original primary rejection is preserved");
            if(name=="invalid-largest-does-not-skip")Check(old is null,"invalid largest raw component does not silently select a smaller primary");
            if(name=="over32-islands")
            {
                var all=(System.Collections.IEnumerable)typeof(StructureFramePreparation).GetMethod("Islands",flags)!.Invoke(owned,null)!;
                Check(all.Cast<object>().Count()==33 && current is not null,"index island budget does not discard the original valid primary");
            }
            if(old is null)return;
            Check(old.Bounds==current!.Bounds && JsonNode.DeepEquals(old.Component,current.Component) && Bytes(old.Query).AsSpan().SequenceEqual(Bytes(current.Query)),
                name+" primary label/tie/component/full mask agrees with original Geometry.Current");
            var chains=Geometry.ObservedChains(original,old);
            if(name=="low-chain-primary")Check(chains.Count>0 && chains.Count<6,"low-chain control is below the index admission threshold but retains a primary");
            Check(JsonNode.DeepEquals(JsonSerializer.SerializeToNode(chains,Json),
                JsonSerializer.SerializeToNode(Geometry.ObservedChains(frame,current),Json)),name+" original primary observed chains agree exactly");
        }
    }
    sealed record Result(JsonObject Index,JsonObject Scan,double ElapsedMs,StructureFramePreparationStats? Preparation);
    static JsonNode Canonical(JsonNode input)
    {
        var copy=input.DeepClone();Visit(copy);return copy;
        static void Visit(JsonNode? node)
        {
            if(node is JsonObject obj)
            {foreach(string key in new[]{"elapsedMs","totalMs","observationMs","resourceMs","gateMs","elapsedSeconds","firstVerifiedPoseElapsedSeconds"})obj.Remove(key);
                foreach(var p in obj)Visit(p.Value);}
            else if(node is JsonArray arr)foreach(var p in arr)Visit(p);
        }
    }
    static byte[] Bytes(Mat mat)
    {using var copy=mat.IsContinuous()?null:mat.Clone();var data=copy??mat;var bytes=new byte[checked(data.Rows*data.Cols*data.ElemSize())];Marshal.Copy(data.Data,bytes,0,bytes.Length);return bytes;}
    static Mat Control(string kind)
    {
        var result=new Mat(800,1600,MatType.CV_8UC3,Scalar.All(5));
        int count=kind=="over32-islands"?33:2;
        for(int i=0;i<count;i++)
        {int size=kind=="invalid-largest-does-not-skip" && i==0?70:40;
            Cv2.Rectangle(result,new Rect(80+(i%12)*100,80+(i/12)*180,size,size),Scalar.All(kind=="invalid-largest-does-not-skip" && i==0?250:130),-1);}
        return result;
    }
}
