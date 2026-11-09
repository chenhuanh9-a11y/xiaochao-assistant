using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Vision;

namespace StarMap.Entry.Tests;

internal static class SyntheticCases
{
    internal sealed record Check(string Name,bool Passed,object Evidence);
    internal static List<Check> Run(SideDoorObservation detector, string iconPath)
    {
        var results = new List<Check>();
        using var islands = new Mat(480,640,MatType.CV_8UC3,Scalar.All(0));
        Cv2.Rectangle(islands,new Rect(108,70,94,84),Scalar.All(125),-1);
        Cv2.Rectangle(islands,new Rect(320,240,60,70),Scalar.All(125),-1);
        Cv2.Rectangle(islands,new Rect(430,200,40,74),Scalar.All(125),-1);
        using var entry = EntryObservation.Capture(islands);
        results.Add(new("entry-roi-clips-padding-and-keeps-only-eligible-secondary-islands",
            entry is not null && entry.Geometry.Bounds == new Rect(102,67,108,95)
            && entry.OtherBounds.SequenceEqual([new Rect(312,232,76,86)]),
            new {primary=entry?.Geometry.Bounds.ToString(),extra=entry?.OtherBounds.Select(r=>r.ToString()).ToArray()}));

        using var blank = new Mat(480,640,MatType.CV_8UC3,Scalar.All(0));
        var none = detector.Observe(blank);
        results.Add(new("passport-no-visible-island",none.Bounds is null && none.Observations.Count==0 && none.Boundary is null,none.Reason));

        // The bright island passes visible_map_component and is excluded by map_color_mask (V > 205).
        // The real icon is outside that island's crop but inside the full HUD. Its 1.3x size occurs only in unusual scales.
        using var scene = blank.Clone();
        Cv2.Rectangle(scene,new Rect(150,150,112,115),Scalar.All(230),-1);
        var icon = JsonNode.Parse(File.ReadAllBytes(iconPath))!["exitIcon"]!;
        int width = icon["width"]!.GetValue<int>(), height = icon["height"]!.GetValue<int>();
        byte[] pixels = Convert.FromBase64String(icon["pixels"]!.GetValue<string>());
        using var gray = new Mat(height,width,MatType.CV_8UC1);
        Marshal.Copy(pixels,0,gray.Data,pixels.Length);
        var enlarged = new Size((int)Math.Round(width*1.3),(int)Math.Round(height*1.3));
        using var scaled = new Mat();
        using var bgr = new Mat();
        Cv2.Resize(gray,scaled,enlarged,0,0,InterpolationFlags.Area);
        Cv2.CvtColor(scaled,bgr,ColorConversionCodes.GRAY2BGR);
        using (var roi = new Mat(scene,new Rect(400,200,enlarged.Width,enlarged.Height))) bgr.CopyTo(roi);
        var unscoped = detector.Observe(scene);
        using var maskGeometry = Geometry.Current(scene);
        bool exactGlyph = unscoped.Anchors.Any(a => a!["x"]!.GetValue<int>()==400 && a["y"]!.GetValue<int>()==200
            && a["width"]!.GetValue<int>()==enlarged.Width && a["height"]!.GetValue<int>()==enlarged.Height);
        results.Add(new("no-context-enlarged-glyph-outside-bright-visible-island",
            unscoped.Bounds == new Rect(142,142,128,131) && maskGeometry is null && exactGlyph && unscoped.Boundary is null,
            new {bounds=unscoped.Bounds.ToString(),expectedSize=enlarged.ToString(),exactGlyph,unscoped.Anchors,unscoped.Boundary}));
        var limited = detector.ObserveBounds(scene,new Rect(142,142,128,131));
        results.Add(new("supplied-crop-retains-local-glyph-search",
            !limited.Anchors.Any(a=>a!["x"]!.GetValue<int>()==400 && a["y"]!.GetValue<int>()==200),limited.Anchors));

        // These rows replace only the rectangle producer to isolate observed_side_boundary's ambiguity policy.
        var rows = new JsonArray(Completed("上",.99),Completed("右",.80));
        var multiple = SideDoorObservation.ObservedBoundary(rows,out int count);
        results.Add(new("two-measured-exits-have-no-boundary",multiple is null && count==2,new {count,multiple}));
        rows[1]!["direction"]!["localRectangleAccepted"] = false;
        var single = SideDoorObservation.ObservedBoundary(rows,out int remaining);
        results.Add(new("unconfirmed-local-rectangle-cannot-compete",remaining==1 && single?["direction"]?.GetValue<string>()=="N"
            && single["center"]![0]!.GetValue<double>()==40 && single["center"]![1]!.GetValue<double>()==20,new {remaining,single}));
        return results;
    }

    private static JsonObject Completed(string name,double score) => new() {
        ["reliable"]=true, ["icon"]=new JsonObject { ["score"]=score },
        ["direction"]=new JsonObject { ["reliable"]=true, ["direction"]=name, ["localRectangleAccepted"]=true,
            ["rectangleBounds"]=new JsonArray(30,20,50,80) }
    };
}
