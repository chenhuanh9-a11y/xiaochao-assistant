using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Vision;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

internal static class StructureFitProbe
{
    public static int ContourOracle(string inputPath,string output)
    {
        if (Directory.Exists(output)) throw new IOException("Use a new oracle directory."); Directory.CreateDirectory(output);
        var source = JsonNode.Parse(File.ReadAllBytes(inputPath))!; var results = new List<object>(); int failures = 0, nativeDifferences = 0;
        foreach (var item in source["cases"]!.AsArray())
        {
            var contour = item!["contour"]!.Deserialize<int[][]>()!.Select(p=>new Point(p[0],p[1])).ToArray();
            var expected = item["expected"]!.Deserialize<int[][]>()!;
            double epsilon = item["epsilon"]!.GetValue<double>();
            var actual = StructureContourApproximation.ApproximateClosed(contour,epsilon).Select(p=>new[]{p.X,p.Y}).ToArray();
            var native = Cv2.ApproxPolyDP(contour,epsilon,true).Select(p=>new[]{p.X,p.Y}).ToArray();
            bool same = actual.Length == expected.Length && actual.Zip(expected).All(p=>p.First.SequenceEqual(p.Second));
            bool nativeSame = native.Length == expected.Length && native.Zip(expected).All(p=>p.First.SequenceEqual(p.Second));
            if (!same) failures++; if (!nativeSame) nativeDifferences++;
            results.Add(new { name=item["name"]!.GetValue<string>(),passed=same,nativeSame,actual,native,expected });
        }
        var summary = new { passed=failures==0 && nativeDifferences>0, count=results.Count, failures,nativeDifferences,results,
            scope="Synthetic integer contours: original native Python OpenCV4.12 expected vs managed original semantics and current native4.13 counterfactual" };
        File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(summary,new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine(JsonSerializer.Serialize(new { summary.passed,summary.count,summary.failures,summary.nativeDifferences })); return summary.passed ? 0 : 1;
    }

    public static int Run(string resources,string inputPath,string output)
    {
        if (Directory.Exists(output)) throw new IOException("Use a new probe directory."); Directory.CreateDirectory(output);
        var json = new JsonSerializerOptions { WriteIndented = true, NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals };
        var input = JsonNode.Parse(File.ReadAllBytes(inputPath))!;
        string Text(string key) => input[key]!.GetValue<string>();
        var catalog = ResourceCatalog.Load(resources); var entry = catalog.Find(Text("mode"),Text("season"),"shared-structure",Text("mapId"),Text("floor"))!;
        var data = catalog.LoadSharedStructure(entry).Value!;
        string fastPath = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(entry.StructureNpzPath))!,"fast-structure",Path.GetFileNameWithoutExtension(entry.StructureNpzPath)+"-fast.npz");
        var prepared = FastStructure.LoadPrepared(fastPath,data);
        using var geometry = StructureGeometry.Open(data);
        using var frame = Cv2.ImDecode(File.ReadAllBytes(Text("image")),ImreadModes.Color);
        using var observation = Geometry.Current(frame,new Rect(0,0,frame.Cols,frame.Rows))!;
        var nodes = Geometry.Nodes(observation); var observed = Geometry.Observed(frame,observation); var chains = Geometry.ObservedChains(frame,observation);
        var fit = FastStructureFit.Evaluate(frame,observation,geometry,prepared);
        Cv2.FindContours(observation.Query,out Point[][] contours,out _,RetrievalModes.List,ContourApproximationModes.ApproxSimple);
        var approximations = contours.Select(contour => new { raw = contour.Select(p=>new[]{p.X,p.Y}).ToArray(),
            native = Cv2.ApproxPolyDP(contour,2,true).Select(p=>new[]{p.X,p.Y}).ToArray(),
            originalSemantics = StructureContourApproximation.ApproximateClosed(contour,2).Select(p=>new[]{p.X,p.Y}).ToArray() }).ToArray();
        File.WriteAllText(Path.Combine(output,"contours.json"),JsonSerializer.Serialize(approximations,json));
        var observationData = new { bounds = new[] {observation.Bounds.X,observation.Bounds.Y,observation.Bounds.Right,observation.Bounds.Bottom},
            component = observation.Component, nodes = nodes.Select((n,i)=>new { index=i, point=new[]{n.Point.X,n.Point.Y},
                rays=n.Rays.Select(p=>new[]{p.X,p.Y}).ToArray(), lengths=n.Lengths, previous=n.Previous,next=n.Next,valid=n.Valid }).ToArray(),
            chains = chains.Select(c=>new { index=c.Index,points=c.Points,rays=c.Rays }).ToArray() };
        File.WriteAllText(Path.Combine(output,"observation.json"),JsonSerializer.Serialize(observationData,json));
        File.WriteAllText(Path.Combine(output,"fast-fit.json"),JsonSerializer.Serialize(fit,json));
        Cv2.ImEncode(".png",observation.Query,out byte[] queryPng); File.WriteAllBytes(Path.Combine(output,"query.png"),queryPng);
        Cv2.ImEncode(".png",observed,out byte[] observedPng); File.WriteAllBytes(Path.Combine(output,"observed.png"),observedPng);
        Console.WriteLine(JsonSerializer.Serialize(new { fit.GeometryVerified,fit.Reason,nodes=nodes.Count,chains=chains.Count,output },json)); return 0;
    }
}
