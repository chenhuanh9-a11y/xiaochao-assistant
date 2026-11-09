using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

public sealed record Corner(Point2d Point, Point2d[] Rays, double[] Lengths, int Previous, int Next, bool Valid);
public sealed record Wall(int Axis, int Sign, double Normal, double Lo, double Hi, int EdgeId = -1);

/// <summary>Lazy structure observations for one immutable BGR frame. The caller owns the frame.</summary>
public sealed class Observation : IDisposable
{
    internal Observation(Mat frame, Mat query, Rect bounds, JsonObject component)
    {
        Frame = frame;
        Query = query;
        Bounds = bounds;
        Component = component;
    }

    internal Mat Frame { get; }
    public Mat Query { get; }
    public Rect Bounds { get; }
    public JsonObject Component { get; }
    internal Mat? Edge { get; set; }
    internal Mat? Observed { get; set; }
    internal List<Corner>? Nodes { get; set; }
    internal List<Wall>? Lines { get; set; }
    internal List<FastObservedChain>? Chains { get; set; }

    internal void RequireFrame(Mat frame)
    {
        if (!ReferenceEquals(frame, Frame))
            throw new ArgumentException("Observation belongs to a different frame.");
        ObjectDisposedException.ThrowIf(Query.IsDisposed, this);
    }

    public void Dispose()
    {
        Observed?.Dispose();
        Edge?.Dispose();
        Query.Dispose();
    }
}
