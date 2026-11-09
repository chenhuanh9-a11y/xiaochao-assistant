using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Vision.Tests;

internal static class Program
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true,
        NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals };

    private static int Main(string[] args)
    {
        if (args.Length is not (6 or 8))
        {
            Console.Error.WriteLine("Use --fixture-index <index.json> --resources <pack root> --output <new directory> [--fit-oracle <original Python directory>].");
            return 2;
        }
        var options = Enumerable.Range(0, args.Length / 2).ToDictionary(i => args[2*i], i => Path.GetFullPath(args[2*i+1]));
        options.TryGetValue("--fit-oracle", out var fitOracle);
        string index = options["--fixture-index"], resources = options["--resources"], output = options["--output"];
        if (Directory.Exists(output)) throw new IOException("Replay output directory already exists.");
        Directory.CreateDirectory(output);
        var catalog = ResourceCatalog.Load(resources);
        var cases = new List<object>();
        bool passed = true, archiveCasesRun = false;
        foreach (var row in Read(index).AsArray())
        {
            string name = row!["case"]!.GetValue<string>();
            if (Path.GetFileName(name) != name || name is "." or "..") throw new InvalidDataException("Invalid case name.");
            string source = Path.Combine(Path.GetDirectoryName(index)!, name), dest = Path.Combine(output, name);
            Directory.CreateDirectory(dest);
            try
            {
                var input = Read(Path.Combine(source, "input.json"));
                var preparedDescription = Read(Path.Combine(source,input["preparedPath"]!.GetValue<string>()));
                string filename = Path.GetFileName(preparedDescription["resourcePath"]!.GetValue<string>());
                string archive = Directory.EnumerateFiles(resources,filename,SearchOption.AllDirectories).Single();
                string stem = Path.GetFileNameWithoutExtension(archive);
                if (!stem.EndsWith("-fast",StringComparison.Ordinal)) throw new InvalidDataException("Expected -fast.npz resource.");
                using var npz = NpzArchive.Open(archive);
                var metadata = JsonNode.Parse(npz.ReadArray("sourceMetadata").ReadUnicodeScalar())!;
                var binding = metadata["binding"]!;
                var entry = catalog.Entries.Single(e => e.MapId == binding["mapId"]!.GetValue<string>()
                    && e.Floor == binding["floor"]!.GetValue<string>() && e.Profile == binding["profile"]!.GetValue<string>()
                    && Path.GetFileNameWithoutExtension(e.StructureNpzPath) == stem[..^5]
                    && Path.GetDirectoryName(Path.GetDirectoryName(e.StructureNpzPath)) == Path.GetDirectoryName(Path.GetDirectoryName(archive)));
                var loaded = catalog.LoadSharedStructure(entry);
                var data = loaded.Value ?? throw new InvalidDataException(loaded.Error?.ToString());
                var prepared = FastStructure.LoadPrepared(archive, data);
                using var geometry = StructureGeometry.Open(data);
                var result = Replay(source,dest,input,prepared,geometry,fitOracle);
                if (!archiveCasesRun)
                {
                    var generated = PreparedComparison.Run(output, data, prepared);
                    Save(Path.Combine(output,"prepare-comparison.json"),generated);
                    passed &= generated.Passed;
                    var boundaries = ArchiveBoundaries.Run(resources,output,archive,entry,data);
                    Save(Path.Combine(output,"archive-boundaries.json"),boundaries);
                    passed &= boundaries.Passed;
                    archiveCasesRun = true;
                }
                passed &= result.Passed;
                cases.Add(new { name, result.Passed, result.Differences, entry.StableKey, result.Reason, result.NumericFitComparedToOracle });
            }
            catch (Exception error)
            {
                passed = false;
                Save(Path.Combine(dest,"error.json"),new { type = error.GetType().Name,error.Message,error.StackTrace });
                cases.Add(new {name, passed = false, error = error.Message});
            }
        }
        passed &= cases.Count > 0 && archiveCasesRun;
        var summary = new { passed, cases, archiveCasesRun };
        Save(Path.Combine(output,"summary.json"),summary);
        Console.WriteLine(JsonSerializer.Serialize(summary,Json));
        return passed ? 0 : 1;
    }

    private static (bool Passed,int Differences,string Reason,bool NumericFitComparedToOracle) Replay(string source,string dest,JsonNode input,
        FastPrepared prepared,StructureGeometry geometry,string? fitOracle)
    {
        string frameFile = input["sourcePath"]!.GetValue<string>();
        if (!Path.IsPathRooted(frameFile)) frameFile = Path.Combine(source,frameFile);
        using var frame = Decode(frameFile, ImreadModes.Color);
        using var context = Geometry.Current(frame) ?? throw new InvalidDataException("Current geometry unavailable.");
        var realEdges = Geometry.Observed(frame,context);
        string? kind = input["kind"]?.GetValue<string>();
        bool replaceEdges = kind == "empty-observed-negative";
        using var emptyEdges = replaceEdges ? new Mat(frame.Size(),MatType.CV_8UC1,Scalar.All(0)) : null;
        var edges = emptyEdges ?? realEdges;
        var prior = input["prior"]?.Deserialize<double[][]>();
        if (prior is not null && (kind != "neutral-local-prior" || !IsIdentity(prior)))
            throw new InvalidDataException("Only the fixture's explicitly neutral identity prior is supported in this replay.");
        bool localOnly = input["localOnly"]!.GetValue<bool>(), cancelled = input["cancelled"]?.GetValue<bool>() == true;
        // Case 4 explicitly replaces current-frame evidence with an empty mask; other cases run the normal image entry.
        var result = replaceEdges
            ? FastAlignment.Propose(edges, Array.Empty<FastObservedChain>(), prepared, prior, localOnly, () => cancelled)
            : FastStructure.Propose(frame,context,prepared,prior,localOnly,() => cancelled);
        var chains = replaceEdges ? new List<FastObservedChain>() : Geometry.ObservedChains(frame,context);
        var standalone = new {
            points = FastAlignment.SampleEdges(edges,300), coarse = FastAlignment.SampleEdges(edges,96),
            pitch = FastAlignment.Pitch(edges), chainSeeds = FastAlignment.ChainSeeds(chains,prepared)
        };
        var fit = replaceEdges ? null : FastStructureFit.Evaluate(frame,context,geometry,prepared,
            prior is null ? null : new StructurePrior(geometry.Source,prior),localOnly,() => cancelled);
        FastProposalResult? roundedPrior = null;
        if (kind == "neutral-local-prior")
        {
            // Roundoff in an otherwise neutral fixed-axis coordinate adapter.
            // Grid reads the same scale/translation as the independent neutral-prior oracle.
            double[][] rounded = [[1,2e-12,0],[-2e-12,1-1e-12,0],[0,0,1]];
            roundedPrior = FastStructure.Propose(frame,context,prepared,rounded,localOnly:true);
        }
        string reason = (string)result.Diagnostics["reason"]!;
        Save(Path.Combine(dest,"actual.json"),new { kind, cancelled, localOnly, context.Bounds, context.Component,
            frameWidth = frame.Cols, frameHeight = frame.Rows, nativeWidth = prepared.Width, nativeHeight = prepared.Height,
            preparedPitch = prepared.Pitch, chainCount = prepared.Chains.Length, observedChains = chains,
            standalone, proposal = result, fit, roundedPrior, fitOracleRequested = fitOracle is not null });
        if (!Cv2.ImWrite(Path.Combine(dest,"observed.png"),edges)) throw new IOException("Writing observed mask failed.");
        if (replaceEdges && !Cv2.ImWrite(Path.Combine(dest,"raw-observed.png"),realEdges)) throw new IOException("Writing raw mask failed.");

        // The expected matrices, retained rows, chain observations and distances never enter the solve.
        var expected = Read(Path.Combine(source,"expected.json"));
        using var oracleEdges = Decode(Path.Combine(source,input["observedPath"]!.GetValue<string>()),ImreadModes.Grayscale);
        var differences = new List<object>();
        var pixelEvidence = ComparePixels(edges,oracleEdges,dest);
        if (pixelEvidence.Different != 0) differences.Add(new { path = "observed", pixelEvidence });
        void Check(string path,JsonNode? reference,object? value) =>
            Compare(path,reference,JsonSerializer.SerializeToNode(value,Json),differences);
        Check("frame.width",input["width"],frame.Cols);
        Check("frame.height",input["height"],frame.Rows);
        Check("standalone.points",expected["points"],standalone.points);
        Check("standalone.coarse",expected["coarse"],standalone.coarse);
        Check("standalone.pitch",expected["pitch"],standalone.pitch);
        Check("standalone.seeds",expected["chainSeeds"],standalone.chainSeeds.Select(Seed));
        Check("observedChains",input["chains"],chains.Select(c => new {points=c.Points,rays=c.Rays}));
        if (!cancelled) Check("trace.points",expected["points"],result.Trace.GetValueOrDefault("points"));
        if (!cancelled && standalone.points.Length >= 40)
        {
            Check("trace.coarse",expected["coarse"],result.Trace.GetValueOrDefault("coarse"));
            if (prior is null)
                Check("trace.seeds",expected["chainSeeds"],TracePoses("chainSeeds").Select(Seed));
        }
        var calls = expected["refineCalls"]!.AsArray();
        var retained = calls.Select(c => new object?[] { c!["inputScale"], c["inputTranslation"] }).ToArray();
        Check("trace.retained",JsonSerializer.SerializeToNode(retained),TracePoses("retained").Select(Seed));
        Check("trace.refined",new JsonArray(calls.Select(c=>c!["output"]!.DeepClone()).ToArray()),TracePoses("allRefined").Select(Pose));
        Check("poses",expected["poses"],result.Poses.Select(Pose));
        var originalDiagnostics = expected["diagnostics"]!.DeepClone();
        var actualDiagnostics = JsonSerializer.SerializeToNode(result.Diagnostics,Json)!;
        RemoveTimings(originalDiagnostics);
        RemoveTimings(actualDiagnostics);
        Check("diagnostics",originalDiagnostics,actualDiagnostics);
        if (roundedPrior is not null)
        {
            Check("rounded-neutral.poses",expected["poses"],roundedPrior.Poses.Select(Pose));
            var roundedDiagnostics = JsonSerializer.SerializeToNode(roundedPrior.Diagnostics,Json)!;
            RemoveTimings(roundedDiagnostics);
            Check("rounded-neutral.diagnostics",originalDiagnostics,roundedDiagnostics);
        }

        bool fitCompared = false;
        if (fitOracle is not null)
        {
            // Both solvers have finished before any fit oracle is opened.
            var expectedFit = Read(Path.Combine(fitOracle,Path.GetFileName(source),"expected-fit.json"));
            if (expectedFit["skipped"] is null)
            {
                fitCompared = true;
                Check("fit.success",expectedFit["success"],fit?.GeometryVerified);
                Check("fit.matrix",expectedFit["matrix"],fit?.Matrix);
                Check("fit.reason",expectedFit["reason"],fit?.GeometryVerified == true ? "experimental-fast-structure" : fit?.Reason);
                var expectedPoint = expectedFit["Point"]?.DeepClone();
                var actualPoint = fit?.Point?.DeepClone();
                expectedPoint?.AsObject().Remove("preprocessAndFitMs");
                actualPoint?.AsObject().Remove("preprocessAndFitMs");
                Check("fit.Point",expectedPoint,actualPoint);
                Check("fit.Regions",expectedFit["Regions"],fit?.Regions);
                Check("fit.Wall",expectedFit["Wall"],fit?.Wall);
                Check("fit.Residual",expectedFit["Residual"],fit?.Residual);
            }
            else if (!replaceEdges) throw new InvalidDataException("Only the explicitly replaced-edge fixture may skip full fit comparison.");
        }
        Save(Path.Combine(dest,"differences.json"),new { pixelEvidence, numericFitComparedToOracle = fitCompared, differences });
        return (differences.Count == 0,differences.Count,reason,fitCompared);

        IEnumerable<FastPose> TracePoses(string key) => result.Trace.TryGetValue(key,out var v) ? (IEnumerable<FastPose>)v! : [];
        static object[] Seed(FastPose p) => [p.Scale, p.Translation];
        static object[] Pose(FastPose p) => [p.Score, p.Scale, p.Translation];
    }

    private static bool IsIdentity(double[][] matrix) => matrix.Length == 3 &&
        matrix.Select((row,i) => row.Length == 3 && row.Select((x,j) => x == (i == j ? 1 : 0)).All(b=>b)).All(b=>b);

    private static void RemoveTimings(JsonNode diagnostics)
    {
        diagnostics.AsObject().Remove("proposalMs");
        if (diagnostics["searchStages"] is JsonArray stages)
            foreach (var stage in stages) stage!.AsObject().Remove("elapsedMs");
    }

    private sealed record PixelDifference(int Different,int MaxDelta,int Width,int Height);

    private static PixelDifference ComparePixels(Mat actual,Mat expected,string dest)
    {
        if (expected.Size() != actual.Size() || expected.Type() != actual.Type()) return new(-1,-1,actual.Cols,actual.Rows);
        using var difference = new Mat();
        Cv2.Absdiff(actual,expected,difference);
        int count = Cv2.CountNonZero(difference);
        Cv2.MinMaxLoc(difference,out _,out double max);
        if (count > 0 && !Cv2.ImWrite(Path.Combine(dest,"edge-difference.png"),difference))
            throw new IOException("Writing edge difference failed.");
        return new(count,(int)max,actual.Cols,actual.Rows);
    }

    private static void Compare(string path, JsonNode? expected, JsonNode? actual,List<object> differences)
    {
        if (expected is JsonArray a && actual is JsonArray b)
        {
            if (a.Count != b.Count) differences.Add(new {path,expectedLength=a.Count,actualLength=b.Count});
            for (int i=0;i<Math.Min(a.Count,b.Count);i++) Compare(path+"/"+i,a[i],b[i],differences);
            return;
        }
        if (expected is JsonObject e && actual is JsonObject v)
        {
            foreach (string key in e.Select(p=>p.Key).Union(v.Select(p=>p.Key),StringComparer.Ordinal))
            {
                if (e.ContainsKey(key) != v.ContainsKey(key))
                    differences.Add(new {path=path+"/"+key,expectedPresent=e.ContainsKey(key),actualPresent=v.ContainsKey(key)});
                else Compare(path+"/"+key,e[key],v[key],differences);
            }
            return;
        }
        if (expected?.GetValueKind() == JsonValueKind.Number && actual?.GetValueKind() == JsonValueKind.Number)
        {
            double eValue = double.Parse(expected.ToJsonString(),System.Globalization.CultureInfo.InvariantCulture);
            double aValue = double.Parse(actual.ToJsonString(),System.Globalization.CultureInfo.InvariantCulture);
            if (eValue != aValue) differences.Add(new {path,expected=eValue,actual=aValue,delta=aValue-eValue});
        }
        else if (!JsonNode.DeepEquals(expected,actual)) differences.Add(new {path,expected,actual});
    }

    private static Mat Decode(string file,ImreadModes mode)
    {
        var image = Cv2.ImDecode(File.ReadAllBytes(file),mode);
        if (!image.Empty()) return image;
        image.Dispose();
        throw new InvalidDataException("Unable to decode "+file);
    }

    private static JsonNode Read(string file) => JsonNode.Parse(File.ReadAllBytes(file))!;
    private static void Save(string path,object value) => File.WriteAllText(path,JsonSerializer.Serialize(value,Json));
}
