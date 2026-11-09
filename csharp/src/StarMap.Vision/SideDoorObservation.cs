using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public sealed record SideDoorObservationResult(JsonArray Anchors, JsonArray Observations,
    JsonObject? Boundary, Rect? Bounds, string Reason);

/// <summary>Evidence from the exit glyph and current wall pixels, in full-client coordinates.</summary>
public sealed partial class SideDoorObservation : IDisposable
{
    private readonly Mat icon;
    private sealed record Anchor(double Score, Rect Bounds, JsonObject Appearance)
    {
        public JsonObject ToJson() => new() { ["score"] = Score, ["x"] = Bounds.X, ["y"] = Bounds.Y,
            ["width"] = Bounds.Width, ["height"] = Bounds.Height, ["appearance"] = Appearance.DeepClone() };
    }

    public SideDoorObservation(string iconResourcePath)
    {
        var image = JsonNode.Parse(File.ReadAllBytes(iconResourcePath))?["exitIcon"] ??
            throw new InvalidDataException("Exit icon is missing.");
        int width = image["width"]!.GetValue<int>(), height = image["height"]!.GetValue<int>();
        byte[] pixels = Convert.FromBase64String(image["pixels"]!.GetValue<string>());
        if (width <= 0 || height <= 0 || pixels.Length != (long)width*height)
            throw new InvalidDataException("Invalid exit icon shape.");
        icon = Geometry.FromBytes(width,height,pixels);
    }

    public void Dispose() => icon.Dispose();

    /// <summary>With a context, use its measured bounds to limit the glyph search. A null context uses passport observation.</summary>
    public SideDoorObservationResult Observe(Mat frame, EntryObservation? context = null, CancellationToken cancellation = default)
    {
        context?.Geometry.RequireFrame(frame);
        return Observe(frame,context?.Geometry.Bounds,cancellation);
    }

    /// <summary>Reuses a selected current-frame island supplied by the caller, including secondary entry islands.</summary>
    public SideDoorObservationResult ObserveBounds(Mat frame, Rect bounds, CancellationToken cancellation = default) =>
        Observe(frame,(Rect?)bounds,cancellation);

    private SideDoorObservationResult Observe(Mat frame, Rect? supplied, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        if (frame.Empty()) return new(new(),new(),null,null,"no-visible-map-component");
        if (frame.Type() != MatType.CV_8UC3) throw new ArgumentException("Expected an 8-bit BGR frame.",nameof(frame));
        Rect? bounds = supplied ?? EntryObservation.VisibleBounds(frame);
        if (bounds is null) return new(new(),new(),null,null,"no-visible-map-component");
        if (bounds.Value.Width < 1 || bounds.Value.Height < 1 || bounds.Value.Intersect(new Rect(0,0,frame.Cols,frame.Rows)) != bounds)
            throw new ArgumentOutOfRangeException(nameof(supplied),"Observation must lie inside the current frame.");
        var anchors = FindAnchors(frame,supplied,cancellation);
        var observations = new JsonArray();
        foreach (var anchor in anchors)
        {
            cancellation.ThrowIfCancellationRequested();
            var b = anchor.Bounds;
            var live = new JsonObject { ["center"] = new JsonArray(b.X+b.Width/2.0,b.Y+b.Height/2.0),
                ["size"] = new JsonArray(b.Width,b.Height), ["score"] = anchor.Score };
            if (anchor.Score < .58)
            {
                observations.Add(new JsonObject { ["icon"] = live, ["rectangle"] = null, ["reliable"] = false,
                    ["direction"] = new JsonObject { ["reliable"] = false, ["reason"] = "icon-below-local-reliability-threshold" } });
                continue;
            }
            var rectangle = LocalRectangle(frame,bounds.Value,live);
            var direction = Direction(bounds.Value,live,rectangle);
            observations.Add(new JsonObject { ["icon"] = live, ["rectangle"] = rectangle, ["direction"] = direction,
                ["reliable"] = anchor.Score >= .78 || Bool(direction,"reliable") });
        }
        var boundary = ObservedBoundary(observations,out var count);
        return new(new JsonArray(anchors.Select(a => (JsonNode)a.ToJson()).ToArray()),observations,boundary,bounds,
            count == 1 ? "unique-measured-exit" : count > 1 ? "multiple-measured-exits" : "no-measured-exit");
    }

    public static JsonObject? ObservedBoundary(JsonArray observations, out int measuredCount)
    {
        measuredCount = 0;
        JsonObject? result = null;
        foreach (var node in observations)
        {
            if (node is not JsonObject row || !Bool(row,"reliable") || row["direction"] is not JsonObject direction
                || !Bool(direction,"reliable") || !Bool(direction,"localRectangleAccepted")) continue;
            string? name = direction["direction"]?.GetValue<string>() switch {
                "左" => "W", "右" => "E", "上" => "N", "下" => "S", _ => null
            };
            if (name is null) continue;
            var b = direction["rectangleBounds"]!.AsArray().Select(n => Numerics.Scalar(n!)).ToArray();
            double cx = name switch { "E" => b[2], "W" => b[0], _ => (b[0]+b[2])/2 };
            double cy = name switch { "S" => b[3], "N" => b[1], _ => (b[1]+b[3])/2 };
            result = new JsonObject { ["center"] = new JsonArray(cx,cy), ["direction"] = name,
                ["iconScore"] = row["icon"]!["score"]!.DeepClone() };
            measuredCount++;
        }
        return measuredCount == 1 ? result : null;
    }

    private List<Anchor> FindAnchors(Mat frame, Rect? supplied, CancellationToken cancellation)
    {
        int w = frame.Cols,h = frame.Rows;
        int x = Numerics.Rint(w*.18),y = Numerics.Rint(h*.10);
        var roi = new Rect(x,y,Numerics.Rint(w*.84)-x,Numerics.Rint(h*.84)-y);
        if (supplied is { } bounds)
        {
            int pad = 2*Math.Max(icon.Cols,icon.Rows);
            roi = roi.Intersect(new Rect(bounds.X-pad,bounds.Y-pad,bounds.Width+2*pad,bounds.Height+2*pad));
        }
        if (roi.Width <= 0 || roi.Height <= 0) return [];
        using var patch = new Mat(frame,roi);
        using var gray = new Mat();
        Cv2.CvtColor(patch,gray,ColorConversionCodes.BGR2GRAY);
        var first = Search(supplied.HasValue ? [1d] : [.80,.90,1,1.1,1.2,1.3]);
        if (first.Count > 0) return first;
        return Search(supplied.HasValue ? [.90,1.1] : [.18,.24,.32,.42,.55,.70]);

        List<Anchor> Search(double[] scales)
        {
            var buckets = new List<Anchor>[scales.Length];
            Parallel.For(0,scales.Length,new ParallelOptions { MaxDegreeOfParallelism = 2, CancellationToken = cancellation },i =>
            {
                var candidates = buckets[i] = new List<Anchor>(5);
                int width = Math.Max(4,Numerics.Rint(icon.Cols*scales[i]));
                int height = Math.Max(4,Numerics.Rint(icon.Rows*scales[i]));
                if (width > roi.Width || height > roi.Height) return;
                using var template = new Mat();
                using var correlation = new Mat();
                Cv2.Resize(icon,template,new Size(width,height),0,0,InterpolationFlags.Area);
                Cv2.MatchTemplate(gray,template,correlation,TemplateMatchModes.CCoeffNormed);
                for (int peak = 0; peak < 5; peak++)
                {
                    Cv2.MinMaxLoc(correlation,out _,out double score,out _,out Point best);
                    if (score < .30) break;
                    var rect = new Rect(best.X+roi.X,best.Y+roi.Y,width,height);
                    candidates.Add(new(score,rect,Appearance(frame,rect)));
                    int radius = Math.Max(width,height);
                    var suppress = new Rect(best.X-radius,best.Y-radius,2*radius+1,2*radius+1)
                        .Intersect(new Rect(0,0,correlation.Cols,correlation.Rows));
                    using var neighbors = new Mat(correlation,suppress);
                    neighbors.SetTo(OpenCvSharp.Scalar.All(-1));
                }
            });
            var distinct = new List<Anchor>(16);
            // Stable score order preserves Python's scale/peak order even when correlations finish out of order.
            foreach (var anchor in buckets.SelectMany(b => b).OrderByDescending(a => a.Score))
            {
                var b = anchor.Bounds;
                bool near = distinct.Any(old => {
                    var r = old.Bounds;
                    double dx = b.X+b.Width/2d - (r.X+r.Width/2d);
                    double dy = b.Y+b.Height/2d - (r.Y+r.Height/2d);
                    return Math.Sqrt(dx*dx+dy*dy) < Math.Max(b.Width,r.Width)*1.25;
                });
                if (!near) distinct.Add(anchor);
                if (distinct.Count == 16) break;
            }
            return distinct.Where(a => AppearanceOk(a.Appearance) && a.Bounds.Width >= Math.Max(10,Numerics.Rint(w*.0045))
                && w*.18 <= a.Bounds.X && a.Bounds.X <= w*.84 && h*.10 <= a.Bounds.Y && a.Bounds.Y <= h*.84
                && !(w*.18 <= a.Bounds.X && a.Bounds.X <= w*.30 && a.Bounds.Y >= h*.70)).ToList();
        }
    }
}
