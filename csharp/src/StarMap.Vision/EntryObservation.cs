using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>The entry-specific crop and secondary islands measured on one immutable full-client BGR frame.</summary>
public sealed class EntryObservation : IDisposable
{
    private sealed record Island(int Label, Rect Pixels, int Area, double CenterX, double CenterY);
    private EntryObservation(Observation geometry, Rect[] otherBounds)
    {
        Geometry = geometry;
        OtherBounds = otherBounds;
    }

    public Observation Geometry { get; }
    public IReadOnlyList<Rect> OtherBounds { get; }

    public static EntryObservation? Capture(Mat frame)
    {
        var islands = Islands(frame, entry: true, out var search);
        if (islands.Length == 0) return null;
        var largest = islands[0];
        var bounds = Pad(largest.Pixels, search);
        var component = new JsonObject {
            ["area"] = largest.Area, ["center"] = new JsonArray(largest.CenterX,largest.CenterY),
            ["width"] = largest.Pixels.Width, ["height"] = largest.Pixels.Height
        };
        var current = global::StarMap.Vision.Geometry.FromComponent(frame, bounds, component);
        if (current is null) return null;
        var others = islands.Skip(1).Where(i => i.Pixels.Width >= 48 && i.Pixels.Height >= 64)
            .Select(i => Pad(i.Pixels,search)).ToArray();
        return new(current, others);
    }

    // The no-context passport query uses visible_map_component's bounds, even if
    // map_reuse_evidence would later reject the crop's gray/brown translation mask.
    internal static Rect? VisibleBounds(Mat frame)
    {
        var islands = Islands(frame, entry: false, out _);
        return islands.Length == 0 ? null : Pad(islands[0].Pixels,new Rect(0,0,frame.Cols,frame.Rows));
    }

    private static Rect Pad(Rect pixels, Rect extent) =>
        new Rect(pixels.X-8,pixels.Y-8,pixels.Width+16,pixels.Height+16).Intersect(extent);

    private static Island[] Islands(Mat frame, bool entry, out Rect search)
    {
        search = default;
        if (frame.Empty()) return [];
        if (frame.Type() != MatType.CV_8UC3) throw new ArgumentException("Expected an 8-bit full-client BGR frame.", nameof(frame));
        int width = frame.Cols, height = frame.Rows;
        int x = (int)(.16*width), y = (int)((entry ? .14 : .22)*height);
        search = new Rect(x,y,(int)((entry ? .86 : .76)*width)-x,(int)((entry ? .90 : .80)*height)-y);
        if (search.Width == 0 || search.Height == 0) return [];
        using var gray = new Mat();
        using var mask = new Mat(frame.Size(),MatType.CV_8UC1,Scalar.All(0));
        Cv2.CvtColor(frame,gray,ColorConversionCodes.BGR2GRAY);
        using (var roi = new Mat(gray,search))
        using (var target = new Mat(mask,search)) Cv2.Threshold(roi,target,74,1,ThresholdTypes.Binary);
        using var labels = new Mat();
        using var stats = new Mat();
        using var centers = new Mat();
        int count = Cv2.ConnectedComponentsWithStats(mask,labels,stats,centers,PixelConnectivity.Connectivity8);
        var valid = new List<Island>();
        for (int i = 1; i < count; i++)
        {
            int area = stats.At<int>(i,4);
            var rect = new Rect(stats.At<int>(i,0),stats.At<int>(i,1),stats.At<int>(i,2),stats.At<int>(i,3));
            if (area < 1000 || (!entry && (rect.Width > (int)(width*.58) || rect.Height > (int)(height*.62)))) continue;
            valid.Add(new(i,rect,area,centers.At<double>(i,0),centers.At<double>(i,1)));
        }
        return valid.OrderByDescending(i => i.Area).ThenBy(i => i.Label).ToArray();
    }

    public void Dispose() => Geometry.Dispose();
}
