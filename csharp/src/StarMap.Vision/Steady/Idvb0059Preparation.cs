using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision.Steady;

/// <summary>Port of this repository's app/idvb0059_steady.py; never commits session state.</summary>
public static partial class Idvb0059Steady
{
    const int MaxSparsePoints = 150, CoarseStride = 4;
    const double MinScale = .40, MaxScale = 2.40, MinScore = .50, MinMargin = .09, MinDistinct = 10;
    const long PreparedCacheMaxBytes = 512L * 1024 * 1024;
    const string Representation = "shared-renderer-footprint-boundary; managed-byte-index";
    static readonly object CacheLock = new();
    static readonly LinkedList<(SharedStructureData Source, Prepared Value)> Cache = new();

    // Managed arrays remain alive for in-flight solvers after LRU eviction. No native Mat is retained.
    sealed record Prepared(byte[] Edge, byte[] K3, byte[] K5, float[] Distance, Point[] ReferencePoints,
        int Width, int Height, JsonObject Binding, string BindingIdentity, double Pitch, double PitchRatio);
    sealed record CurrentObservation(Point[] Points, byte[] Observed, byte[] Proposal, byte[] Valid,
        int Width, int Height, Point Origin);

    static (JsonObject Binding, string Identity) ReadBinding(SharedStructureData reference)
    {
        var result = new JsonObject();
        if (reference.Metadata.ValueKind == JsonValueKind.Object && reference.Metadata.TryGetProperty("binding", out var row)
            && row.ValueKind == JsonValueKind.Object)
            foreach (var property in row.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                result[property.Name] = property.Value.ValueKind == JsonValueKind.String ? property.Value.GetString() : property.Value.ToString();
        return (result, result.ToJsonString());
    }

    static Prepared Prepare(SharedStructureData reference)
    {
        ArgumentNullException.ThrowIfNull(reference);
        lock (CacheLock)
        {
            for (var item = Cache.First; item is not null; item = item.Next)
                if (ReferenceEquals(item.Value.Source, reference))
                { var hit = item.Value.Value; Cache.Remove(item); Cache.AddLast((reference, hit)); return hit; }
        }
        var raw = reference.CleanMask;
        if (raw is null || raw.Rank != 2 || raw.Shape[0] <= 0 || raw.Shape[1] <= 0
            || raw.Dtype is not ("|u1" or "<u1" or "=u1" or ">u1"))
            throw new ArgumentException("ReferenceCleanMaskInvalid");
        int width = raw.Shape[1], height = raw.Shape[0];
        var bytes = raw.Bytes.ToArray();
        for (int i = 0; i < bytes.Length; i++) bytes[i] = bytes[i] > 0 ? (byte)1 : (byte)0;
        using var mask = Geometry.FromBytes(width, height, bytes);
        using var k3 = Geometry.Kernel(3); using var k5 = Geometry.Kernel(5);
        using var edge = new Mat();
        Cv2.MorphologyEx(mask, edge, MorphTypes.Gradient, k3);
        if (reference.UnknownMask is { } unknown)
        {
            if (unknown.Rank != 2 || unknown.Shape[0] != height || unknown.Shape[1] != width)
                throw new ArgumentException("ReferenceUnknownMaskShapeMismatch");
            var unknownBytes = new byte[bytes.Length];
            for (int i = 0; i < unknownBytes.Length; i++) unknownBytes[i] = unknown.ReadNumericValue(i) > 0 ? (byte)1 : (byte)0;
            using var unknownMat = Geometry.FromBytes(width, height, unknownBytes);
            Cv2.Dilate(unknownMat, unknownMat, k3); edge.SetTo(Scalar.All(0), unknownMat);
        }
        if (Cv2.CountNonZero(edge) < 300) throw new ArgumentException("ReferenceInsufficientEdges");
        using var dilated3 = new Mat(); using var dilated5 = new Mat(); using var distance = new Mat();
        Cv2.Dilate(edge, dilated3, k3); Cv2.Dilate(edge, dilated5, k5);
        var edgeBytes = Geometry.Bytes(edge);
        using var inverse = Geometry.FromBytes(width, height, edgeBytes.Select(p => (byte)(1-p)).ToArray());
        Cv2.DistanceTransform(inverse, distance, DistanceTypes.L2, DistanceTransformMasks.Precise);
        var points = Nonzero(edgeBytes, width);
        var signal = new double[width]; foreach (var p in points) signal[p.X]++;
        var (pitch, ratio) = DominantPitch(signal, interpolate: false);
        var binding = ReadBinding(reference);
        var prepared = new Prepared(edgeBytes, Geometry.Bytes(dilated3), Geometry.Bytes(dilated5), Geometry.Floats(distance),
            points, width, height, binding.Binding, binding.Identity, pitch, ratio);
        lock (CacheLock)
        {
            // Another solver may have prepared this resource concurrently. Reuse the published immutable value.
            for (var item = Cache.First; item is not null; item = item.Next)
                if (ReferenceEquals(item.Value.Source, reference))
                { var hit = item.Value.Value; Cache.Remove(item); Cache.AddLast((reference, hit)); return hit; }
            Cache.AddLast((reference, prepared));
            while (Cache.Count > 6) Cache.RemoveFirst();
            // Match Python's soft retained-array budget, including its explicit oversized-singleton exception.
            while (Cache.Count > 1 && RetainedBytes() > PreparedCacheMaxBytes) Cache.RemoveFirst();
        }
        return prepared;
    }

    static long RetainedBytes()
    {
        long total = 0;
        var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
        void Add(object owner, long size) { if (seen.Add(owner)) total += size; }
        foreach (var (source, prepared) in Cache)
        {
            foreach (var array in new[] { source.CleanMask, source.ValidationMask, source.KnownBoundary, source.UnknownMask })
            {
                if (array is null) continue;
                if (MemoryMarshal.TryGetArray(array.Bytes, out ArraySegment<byte> segment) && segment.Array is { } owner)
                    Add(owner, owner.LongLength);
                else Add(array, array.Bytes.Length);
            }
            Add(prepared.Edge, prepared.Edge.LongLength); Add(prepared.K3, prepared.K3.LongLength); Add(prepared.K5, prepared.K5.LongLength);
            Add(prepared.Distance, prepared.Distance.LongLength*4); Add(prepared.ReferencePoints, prepared.ReferencePoints.LongLength*8);
        }
        return total;
    }

    public static JsonObject PreparedMemorySnapshot()
    {
        lock (CacheLock)
        {
            long bytes = RetainedBytes();
            return new JsonObject { ["entries"] = Cache.Count, ["retainedArrayBytes"] = bytes, ["maxEntries"] = 6,
                ["budgetBytes"] = PreparedCacheMaxBytes, ["oversizedSingleton"] = Cache.Count == 1 && bytes > PreparedCacheMaxBytes,
                ["accounting"] = "unique-backing-arrays; excludes-metadata-temporaries-other-holders" };
        }
    }

    static Point[] Nonzero(byte[] mask, int width)
    {
        var points = new List<Point>();
        for (int i = 0; i < mask.Length; i++) if (mask[i] != 0) points.Add(new(i%width, i/width));
        return points.ToArray();
    }

    static CurrentObservation Extract(Mat frame)
    {
        if (frame is null || frame.Empty() || frame.Depth() != MatType.CV_8U) throw new ArgumentException("FrameInvalid");
        if (frame.Channels() is not (1 or 3 or 4)) throw new ArgumentException("FrameChannelsUnsupported");
        using var full = new Mat();
        if (frame.Channels() == 1) Cv2.CvtColor(frame, full, ColorConversionCodes.GRAY2BGR);
        else if (frame.Channels() == 4) Cv2.CvtColor(frame, full, ColorConversionCodes.BGRA2BGR);
        else frame.CopyTo(full);
        int cw = frame.Cols, ch = frame.Rows, vx = (int)Math.Floor(.10*cw), vy = (int)Math.Floor(.12*ch);
        int right = (int)Math.Ceiling(.88*cw), bottom = (int)Math.Ceiling(.94*ch);
        using var bgr = new Mat(full, new Rect(vx, vy, right-vx, bottom-vy));
        int w = bgr.Cols, h = bgr.Rows;
        using var hsv = new Mat(); Cv2.CvtColor(bgr, hsv, ColorConversionCodes.BGR2HSV);
        using var exclusion = new Mat(h, w, MatType.CV_8UC1, Scalar.All(0));
        (double X, double Y, double W, double H)[] regions = [(0,0,.18*cw,ch),(.185*cw,ch-.245*cw,.15*cw,.215*cw),
            (.18*cw,0,.155*cw,.235*cw),(.75*cw,0,.14*cw,.07*cw),(.91*cw,0,.09*cw,ch),
            (.52*cw,ch-.09*cw,.23*cw,.09*cw),(.80*cw,ch-.12*cw,.20*cw,.12*cw)];
        foreach (var r in regions)
        {
            int x0 = Math.Max(0,(int)Math.Floor(r.X-vx)), y0 = Math.Max(0,(int)Math.Floor(r.Y-vy));
            int x1 = Math.Min(w,(int)Math.Ceiling(r.X+r.W-vx)), y1 = Math.Min(h,(int)Math.Ceiling(r.Y+r.H-vy));
            if (x1 > x0 && y1 > y0) { using var roi = new Mat(exclusion,new Rect(x0,y0,x1-x0,y1-y0)); roi.SetTo(Scalar.All(255)); }
        }
        using var room = new Mat(); using var roomRed = new Mat(); using var corridor = new Mat();
        Cv2.InRange(hsv,new Scalar(0,18,82),new Scalar(25,165,200),room);
        Cv2.InRange(hsv,new Scalar(170,18,82),new Scalar(179,165,200),roomRed); Cv2.BitwiseOr(room,roomRed,room);
        Cv2.InRange(hsv,new Scalar(95,14,82),new Scalar(130,105,200),corridor);
        using var k3 = Geometry.Kernel(3); using var k5 = Geometry.Kernel(5); using var k11 = Geometry.Kernel(11);
        using var candidates = new Mat(h,w,MatType.CV_8UC1,Scalar.All(0));
        var batch = new List<Point[]>();
        foreach (var semantic in new[] { room, corridor })
        {
            semantic.SetTo(Scalar.All(0),exclusion); Cv2.MorphologyEx(semantic,semantic,MorphTypes.Open,k5);
            Cv2.MorphologyEx(semantic,semantic,MorphTypes.Close,k3);
            Cv2.FindContours(semantic,out Point[][] contours,out HierarchyIndex[] hierarchy,RetrievalModes.CComp,ContourApproximationModes.ApproxSimple);
            for (int i = 0; i < contours.Length; i++)
            {
                if (Cv2.ArcLength(contours[i],true) < 30) continue;
                if (hierarchy[i].Parent != -1 && Math.Abs(Cv2.ContourArea(contours[i])) < 900) continue;
                batch.Add(StructureContourApproximation.ApproximateClosed(contours[i],.55));
            }
        }
        if (batch.Count > 0) Cv2.DrawContours(candidates,batch,-1,Scalar.All(255),2,LineTypes.Link8);
        using var excluded = new Mat(); Cv2.Dilate(exclusion,excluded,k5); candidates.SetTo(Scalar.All(0),excluded);
        var bounds = Cv2.BoundingRect(candidates);
        int padding = Math.Max(8,Numerics.Rint(cw*.03));
        if (bounds.Width == 0 || bounds.Height == 0)
        {
            using var validEmpty = new Mat(); Cv2.BitwiseNot(excluded,validEmpty);
            return new([],new byte[h*w],new byte[h*w],Geometry.Bytes(validEmpty),w,h,new(vx,vy));
        }
        Rect Padded(Rect b,int width,int height) { int x=Math.Max(0,b.X-padding),y=Math.Max(0,b.Y-padding);
            return new(x,y,Math.Min(width,b.Right+padding)-x,Math.Min(height,b.Bottom+padding)-y); }
        var envelope = Padded(bounds,w,h);
        using var patch = new Mat(bgr,envelope); using var semanticCrop = new Mat(candidates,envelope); using var excludedCrop = new Mat(excluded,envelope);
        vx += envelope.X; vy += envelope.Y;
        using var gray = new Mat(); Cv2.CvtColor(patch,gray,ColorConversionCodes.BGR2GRAY);
        using var strong = new Mat(); Cv2.Canny(gray,strong,80,180,3,true); Cv2.Dilate(strong,strong,k5);
        using var blurred = new Mat(); Cv2.GaussianBlur(gray,blurred,new Size(5,5),1.5);
        using var detail = new Mat(); Cv2.Subtract(gray,blurred,detail); Cv2.Threshold(detail,detail,4,255,ThresholdTypes.Binary); Cv2.Dilate(detail,detail,k3);
        using var weak = new Mat(); Cv2.Canny(gray,weak,25,65,3,true); Cv2.BitwiseAnd(weak,detail,weak); Cv2.Dilate(weak,weak,k3);
        using var support = new Mat(); Cv2.BitwiseOr(strong,weak,support);
        using var observed = new Mat(); using var proposal = new Mat();
        Cv2.BitwiseAnd(semanticCrop,support,observed); Cv2.BitwiseAnd(semanticCrop,strong,proposal);
        using var frontier = new Mat(); Cv2.BitwiseNot(support,frontier); Cv2.BitwiseAnd(semanticCrop,frontier,frontier); Cv2.Dilate(frontier,frontier,k11);
        using var valid = new Mat(); Cv2.BitwiseOr(frontier,excludedCrop,valid); Cv2.BitwiseNot(valid,valid); valid.SetTo(Scalar.All(255),observed);
        bounds = Cv2.BoundingRect(proposal);
        var crop = bounds.Width > 0 && bounds.Height > 0 ? Padded(bounds,proposal.Cols,proposal.Rows) : new Rect(0,0,proposal.Cols,proposal.Rows);
        using var finalObserved = new Mat(observed,crop); using var finalProposal = new Mat(proposal,crop); using var finalValid = new Mat(valid,crop);
        vx += crop.X; vy += crop.Y;
        var proposalBytes = Geometry.Bytes(finalProposal); var dense = Nonzero(proposalBytes,crop.Width);
        int count = Math.Min(dense.Length,MaxSparsePoints); var points = new Point[count];
        for (int i = 0; i < count; i++) points[i] = dense[(int)((double)i*dense.Length/count)];
        return new(points,Geometry.Bytes(finalObserved),proposalBytes,Geometry.Bytes(finalValid),crop.Width,crop.Height,new(vx,vy));
    }
}
