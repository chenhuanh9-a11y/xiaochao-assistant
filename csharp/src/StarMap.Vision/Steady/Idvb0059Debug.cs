using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision.Steady;

// Friend-only numerical evidence. Every array is copied; tests cannot mutate prepared cache state.
internal sealed record SteadyPreparedDebug(int Width,int Height,byte[] Edge,byte[] K3,byte[] K5,float[] Distance,
    Point[] ReferencePoints,double Pitch,double PitchRatio);
internal sealed record SteadyObservationDebug(int Width,int Height,Point Origin,Point[] Points,byte[] Observed,
    byte[] Proposal,byte[] Valid);

public static partial class Idvb0059Steady
{
    internal static SteadyPreparedDebug InspectPrepared(SharedStructureData reference)
    {
        var value=Prepare(reference);
        return new(value.Width,value.Height,value.Edge.ToArray(),value.K3.ToArray(),value.K5.ToArray(),value.Distance.ToArray(),
            value.ReferencePoints.ToArray(),value.Pitch,value.PitchRatio);
    }

    internal static SteadyObservationDebug InspectObservation(Mat frame)
    {
        var value=Extract(frame);
        return new(value.Width,value.Height,value.Origin,value.Points.ToArray(),value.Observed.ToArray(),value.Proposal.ToArray(),value.Valid.ToArray());
    }
}
