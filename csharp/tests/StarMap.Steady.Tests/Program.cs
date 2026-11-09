using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Vision;
using StarMap.Vision.Steady;

internal static class Program
{
    static readonly JsonSerializerOptions Json=new() { WriteIndented=true,NumberHandling=JsonNumberHandling.AllowNamedFloatingPointLiterals };
    static string Text(JsonNode row,string name)=>row[name]?.GetValue<string>()??throw new InvalidDataException("Missing "+name);
    static JsonNode Read(string path)=>JsonNode.Parse(File.ReadAllBytes(path))??throw new InvalidDataException("Empty JSON");
    static void Save(string path,JsonNode value)=>File.WriteAllText(path,value.ToJsonString(Json));
    static JsonArray Matrix(double[][] matrix)=>new(matrix.Select(row=>(JsonNode)new JsonArray(row.Select(value=>(JsonNode?)JsonValue.Create(value)).ToArray())).ToArray());
    static double[][] ReadMatrix(JsonNode matrix)=>matrix.AsArray().Select(row=>row!.AsArray().Select(value=>value!.GetValue<double>()).ToArray()).ToArray();
    static JsonObject Result(SteadySolveResult value)=>new() { ["accepted"]=value.Accepted,["reason"]=value.Reason,
        ["matrixSourceToClient"]=value.MatrixSourceToClient is { } matrix?Matrix(matrix):null,["confidence"]=value.Confidence,
        ["diagnostics"]=value.Diagnostics.DeepClone(),["coverageState"]=value.ProposedCoverage?.Snapshot() };

    static int Main(string[] args)
    {
        if(args.Length is <2 or >4)
        {Console.Error.WriteLine("StarMap.Steady.Tests <cases.json> <new-output-directory> [--numeric-only|--wallclock-only] [--case=<id>]");return 2;}
        string output=Path.GetFullPath(args[1]);if(Directory.Exists(output)||File.Exists(output))throw new IOException("Use a new output directory.");Directory.CreateDirectory(output);
        var input=Read(args[0]);string resources=Text(input,"resourceRoot");
        var catalog=ResourceCatalog.Load(resources);var rows=new JsonArray();var failures=new JsonArray();
        string? filter=args.FirstOrDefault(a=>a.StartsWith("--case=",StringComparison.Ordinal))?[7..];
        string[] modes=args.Contains("--numeric-only")?["numeric-zero"]:args.Contains("--wallclock-only")?["wallclock"]:["numeric-zero","wallclock"];
        foreach(var caseNode in input["cases"]!.AsArray())
        {
            var source=caseNode!.AsObject();string id=Text(source,"id");if(filter is not null&&id!=filter)continue;
            foreach(string clockMode in modes)
            {
                string dest=Path.Combine(output,id,clockMode);Directory.CreateDirectory(dest);
                try{var result=RunCase(resources,catalog,source,dest,clockMode);rows.Add(result);if(result["passed"]!.GetValue<bool>()!=true)failures.Add(id+"/"+clockMode);}
                catch(Exception error){var failed=new JsonObject { ["id"]=id,["clockMode"]=clockMode,["passed"]=false,["error"]=error.ToString() };rows.Add(failed);failures.Add(id+"/"+clockMode);Save(Path.Combine(dest,"error.json"),failed);}
            }
        }
        if(rows.Count==0)failures.Add("No matching cases");
        var summary=new JsonObject { ["passed"]=failures.Count==0,["cases"]=rows,["failures"]=failures,
            ["preparedCache"]=Idvb0059Steady.PreparedMemorySnapshot(),
            ["scope"]="Offline numerical component replay; own cold Fit -> own coverage -> current-frame 0059. No Live capability, Python process, game or GUI operation.",
            ["comparison"]="numeric-zero: original algorithm diagnostics/pose/state except timings, representation and process identity; wallclock: acceptance/reason and pre-precision fields only. Absolute 1e-5 + relative 1e-8 float tolerance." };
        Save(Path.Combine(output,"summary.json"),summary);
        Console.WriteLine(new JsonObject { ["passed"]=failures.Count==0,["caseRuns"]=rows.Count,["failures"]=failures.DeepClone(),["output"]=output }.ToJsonString());
        return failures.Count==0?0:1;
    }

    static JsonObject RunCase(string resources,ResourceCatalog catalog,JsonObject source,string output,string clockMode)
    {
        string season=Text(source,"season"),mode=Text(source,"mode"),map=Text(source,"mapId"),floor=Text(source,"floor");
        var passport=EntryPassportCatalog.Load(resources,season,mode);
        if(!passport.MapProfiles.TryGetValue(map,out string? profile))throw new InvalidDataException("Map absent from exact entry passport");
        var target=catalog.Find(mode,season,profile,map,floor)??throw new InvalidDataException("Exact resource entry absent");
        var seed=source["seed"]!.AsObject();using var seedFrame=Decode(seed);
        var seedProof=CaptureBinding(source,seed,seedFrame);Save(Path.Combine(output,"seed-proof.json"),seedProof);
        JsonObject cold;SharedStructureData reference;
        if(season=="s0")
        {
            using var host=new S0HostFit(catalog,passport);
            var value=host.Fit(seedFrame,target,options:new S0HostFitOptions { SteadyRequested=false });cold=value.Fit;
            reference=value.Reference??throw new InvalidDataException("Cold S0 Fit did not consume a reference");
        }
        else
        {
            using var pipeline=new SharedStructureFitPipeline(catalog);cold=pipeline.Fit(seedFrame,target);
            reference=catalog.LoadSharedStructure(target).Value??throw new InvalidDataException("Reference unavailable");
        }
        Save(Path.Combine(output,"cold-fit.json"),cold);
        if(cold["success"]?.GetValue<bool>()!=true||cold["matrixSourceToClient"] is not JsonArray)
            throw new InvalidDataException("Own current-run cold Fit rejected; no oracle scale may substitute for it");
        var committed=ReadMatrix(cold["matrixSourceToClient"]!);double scale=committed[0][0];
        SteadyCoverageState state=Idvb0059Steady.ObserveCoverage(seedFrame,reference,committed);
        Save(Path.Combine(output,"seed-coverage.json"),state.Snapshot());
        var checks=BoundaryChecks(seedFrame,reference,scale,state);Save(Path.Combine(output,"boundary-checks.json"),checks);
        bool passed=checks["passed"]!.GetValue<bool>();var steps=new JsonArray();
        if(source["preparedDebug"] is JsonObject preparedExpected)
        {
            var intermediate=IntermediateEvidence.Prepared(reference,preparedExpected,output);Save(Path.Combine(output,"prepared-comparison.json"),intermediate);
            passed&=intermediate["passed"]!.GetValue<bool>();
        }
        int ordinal=0;
        foreach(var stepNode in source["steps"]!.AsArray())
        {
            var step=stepNode!.AsObject();string id=Text(step,"id"),dest=Path.Combine(output,$"step-{ordinal:D3}-{id}");Directory.CreateDirectory(dest);
            using var frame=Decode(step);Save(Path.Combine(dest,"capture-proof.json"),CaptureBinding(source,step,frame));
            string previous=state.Snapshot().ToJsonString();double inputScale=scale;
            var actual=clockMode=="numeric-zero"
                ?Idvb0059Steady.SolveWithPrecisionClock(frame,reference,scale,state,true,false,null,()=>0)
                :Idvb0059Steady.Solve(frame,reference,scale,state);
            var actualJson=Result(actual);Save(Path.Combine(dest,"actual.json"),actualJson);
            var differences=new List<string>();if(state.Snapshot().ToJsonString()!=previous)differences.Add("Input coverage mutated");
            if(!actual.Accepted&&(actual.MatrixSourceToClient is not null||actual.ProposedCoverage is not null))differences.Add("Rejected result proposed pose/state");
            if(step["observationDebug"] is JsonObject observationExpected)
            {
                var intermediate=IntermediateEvidence.Observation(frame,observationExpected,dest);Save(Path.Combine(dest,"observation-comparison.json"),intermediate);
                if(!intermediate["passed"]!.GetValue<bool>())differences.Add("Intermediate observation array mismatch");
            }
            // The independent oracle is first read here, after the C# solver has already returned.
            var expected=step[clockMode=="numeric-zero"?"expected":"expectedWallclock"];
            if(expected is null)differences.Add("Missing independent "+clockMode+" oracle");
            else
            {
                Save(Path.Combine(dest,"expected.json"),expected.DeepClone());
                if(clockMode=="numeric-zero")Compare(expected,actualJson,"$",differences);
                else CompareWallclock(expected,actualJson,differences);
            }
            if(actual.Accepted)
            {
                if(actual.MatrixSourceToClient is null||actual.ProposedCoverage is null)differences.Add("Accepted result missing pose/state");
                else{committed=actual.MatrixSourceToClient;scale=committed[0][0];state=actual.ProposedCoverage;}
            }
            bool ok=differences.Count==0;passed&=ok;
            var report=new JsonObject { ["id"]=id,["ordinal"]=ordinal,["output"]=dest,["passed"]=ok,["inputScale"]=inputScale,["accepted"]=actual.Accepted,["reason"]=actual.Reason,
                ["clockMode"]=clockMode,["precisionTermination"]=actual.Diagnostics["precision"]?["termination"]?.DeepClone(),
                ["differences"]=new JsonArray(differences.Select(d=>(JsonNode?)JsonValue.Create(d)).ToArray()) };
            Save(Path.Combine(dest,"comparison.json"),report);steps.Add(report);
            ordinal++;
        }
        var result=new JsonObject { ["id"]=Text(source,"id"),["clockMode"]=clockMode,["passed"]=passed,["resourceKey"]=target.StableKey,
            ["seedScaleSource"]="this-run-own-cold-Fit.matrixSourceToClient[0][0]",["seedScale"]=ReadMatrix(cold["matrixSourceToClient"]!)[0][0],
            ["scope"]=source["scope"]?.DeepClone(),["steps"]=steps };
        Save(Path.Combine(output,"case-summary.json"),result);return result;
    }

    static Mat Decode(JsonObject row)
    {
        var result=Cv2.ImDecode(File.ReadAllBytes(Text(row,"image")),ImreadModes.Color);
        if(result.Empty()){result.Dispose();throw new InvalidDataException("Original PNG decode failed");}return result;
    }

    static JsonObject CaptureBinding(JsonObject context,JsonObject sample,Mat frame)
    {
        string image=Path.GetFullPath(Text(sample,"image"));var size=sample["physicalClient"]!.AsObject();
        if(frame.Cols!=size["Width"]!.GetValue<int>()||frame.Rows!=size["Height"]!.GetValue<int>())throw new InvalidDataException("PNG is not the declared complete physical client");
        string rawTime=Text(sample,"capturedAt");
        if(!(rawTime.EndsWith('Z')||System.Text.RegularExpressions.Regex.IsMatch(rawTime,@"[+-]\d{2}:\d{2}$"))||
            !DateTimeOffset.TryParse(rawTime,CultureInfo.InvariantCulture,DateTimeStyles.None,out var captured)||
            DateOnly.FromDateTime(captured.Date)<new DateOnly(2026,11,5))throw new InvalidDataException("Capture timestamp is old or unzoned");
        string journal=Path.GetFullPath(Text(sample,"captureRecord"));var record=Read(journal);
        string original=Path.GetFullPath(Path.Combine(Path.GetDirectoryName(journal)!,Text(record,"samplePath")));
        var comparison=OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal;
        if(!string.Equals(image,original,comparison)||!string.Equals(Path.GetDirectoryName(image),Path.GetDirectoryName(journal),comparison)||
            Text(record,"evidenceKind")!="live-client-capture"||Text(record,"eventKind")!="physical-g-client-capture"||
            record["cropProvenance"]?.ToString()=="color-component-unvalidated"||Text(record,"capturedAt")!=rawTime)
            throw new InvalidDataException("Original capture record does not bind this physical G full-client PNG");
        if(!JsonNode.DeepEquals(record["Metadata"]?["Before"],record["Metadata"]?["After"])||!JsonNode.DeepEquals(record["Metadata"]?["Before"],size))
            throw new InvalidDataException("Capture geometry changed or differs from source record");
        var label=record["userLabel"]!;
        if(!JsonNode.DeepEquals(label,record["labelAtCommit"])||record["labelIsUserSupplied"]?.GetValue<bool>()!=true||
            Text(label,"Season")!=Text(context,"season")||Text(label,"Mode")!=Text(context,"mode")||
            (sample["labelGeneration"] is { } generation&&!JsonNode.DeepEquals(label["Generation"],generation))||
            (sample["event"] is { } number&&!JsonNode.DeepEquals(record["Sequence"],number)))
            throw new InvalidDataException("Capture user label differs from committed source metadata");
        return new JsonObject { ["image"]=image,["captureRecord"]=journal,["capturedAt"]=rawTime,["physicalClient"]=size.DeepClone(),
            ["fullClientValidated"]=true,["scope"]="Original capture provenance/date/full-client geometry; map/floor is explicit case selection, not inferred truth" };
    }

    static JsonObject BoundaryChecks(Mat frame,SharedStructureData reference,double scale,SteadyCoverageState state)
    {
        string before=state.Snapshot().ToJsonString();var rows=new JsonArray();bool passed=true;
        void Check(string id,SteadySolveResult result,string reason)
        {
            bool ok=!result.Accepted&&result.Reason==reason&&result.MatrixSourceToClient is null&&result.ProposedCoverage is null&&state.Snapshot().ToJsonString()==before;
            rows.Add(new JsonObject { ["id"]=id,["passed"]=ok,["reason"]=result.Reason });passed&=ok;
        }
        Check("trusted-scale-missing",Idvb0059Steady.Solve(frame,reference,null,state),"TrustedFloorScaleMissing");
        Check("trusted-scale-nan",Idvb0059Steady.Solve(frame,reference,double.NaN,state),"TrustedFloorScaleOutOfRange");
        Check("trusted-scale-low",Idvb0059Steady.Solve(frame,reference,.399999,state),"TrustedFloorScaleOutOfRange");
        Check("trusted-scale-high",Idvb0059Steady.Solve(frame,reference,2.400001,state),"TrustedFloorScaleOutOfRange");
        Check("immediate-cancellation",Idvb0059Steady.Solve(frame,reference,scale,state,cancelled:()=>true),"Cancelled");
        using var empty=new Mat();Check("empty-frame",Idvb0059Steady.Solve(empty,reference,scale,state),"FrameInvalid");
        using var black=new Mat(frame.Size(),MatType.CV_8UC3,Scalar.All(0));
        Check("synthetic-no-current-evidence",Idvb0059Steady.Solve(black,reference,scale,state),"NoCurrentFrameStructuralPoints");
        return new JsonObject { ["passed"]=passed,["checks"]=rows,["scope"]="Scale/cancellation/error boundaries using a real reference; black no-evidence frame explicitly synthetic" };
    }

    static void CompareWallclock(JsonNode expected,JsonNode actual,List<string> differences)
    {
        foreach(string name in new[] {"accepted","reason","confidence"})Compare(expected[name],actual[name],"$."+name,differences);
        foreach(string name in new[] {"inputScale","sameFrameExtractionCount","observationBounds","sparsePointCount","observedEdgePixels","proposalEdgePixels","scalePolicy","selectedBootstrap"})
            if(expected["diagnostics"]?[name] is { } value)Compare(value,actual["diagnostics"]?[name],"$.diagnostics."+name,differences);
    }

    static void Compare(JsonNode? expected,JsonNode? actual,string path,List<string> differences)
    {
        if(differences.Count>=100)return;
        if(expected is null||actual is null){if(expected is not null||actual is not null)differences.Add(path+": null/missing mismatch");return;}
        if(expected is JsonObject obj)
        {
            if(actual is not JsonObject value){differences.Add(path+": object expected");return;}
            foreach(var row in obj)
            {
                if(row.Key is "timingMs" or "milliseconds" or "referenceToken" or "referenceRepresentation")continue;
                Compare(row.Value,value[row.Key],path+"."+row.Key,differences);
            }
            return;
        }
        if(expected is JsonArray array)
        {
            if(actual is not JsonArray value||value.Count!=array.Count){differences.Add(path+": array length mismatch");return;}
            for(int i=0;i<array.Count;i++)Compare(array[i],value[i],path+"["+i+"]",differences);return;
        }
        if(expected is JsonValue ev&&actual is JsonValue av&&ev.TryGetValue<double>(out double e)&&av.TryGetValue<double>(out double a))
        {
            if(!(e==a||Math.Abs(e-a)<=1e-5+1e-8*Math.Max(Math.Abs(e),Math.Abs(a))))differences.Add(path+": "+e.ToString("R",CultureInfo.InvariantCulture)+" != "+a.ToString("R",CultureInfo.InvariantCulture));
        }
        else if(!JsonNode.DeepEquals(expected,actual))differences.Add(path+": "+expected.ToJsonString()+" != "+actual.ToJsonString());
    }
}
