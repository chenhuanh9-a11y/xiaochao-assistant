using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision;

/// <summary>Owned OpenCV masks for one exact catalog reference. Reuse within its resource lifetime.</summary>
public sealed class StructureGeometry : IDisposable
{
    private StructureGeometry(SharedStructureData source, Mat clean, Mat validation, Mat? known, Mat? unknown, List<Wall>? walls)
    {
        Source = source; Clean = clean; Validation = validation; KnownBoundary = known; Unknown = unknown; NativeWalls = walls;
    }

    public SharedStructureData Source { get; }
    public Mat Clean { get; }
    public Mat Validation { get; }
    public Mat? KnownBoundary { get; }
    public Mat? Unknown { get; }
    public IReadOnlyList<Wall>? NativeWalls { get; }

    public static StructureGeometry Open(SharedStructureData source)
    {
        if (source.CleanMask is null) throw new InvalidDataException("fast-clean-mask-unavailable");
        var mats = new List<Mat>();
        Mat Read(NpyArray array)
        {
            var m = Geometry.FromBytes(array.Shape[1], array.Shape[0], array.Bytes.ToArray());
            mats.Add(m);
            Cv2.Threshold(m, m, 0, 1, ThresholdTypes.Binary);
            return m;
        }
        try
        {
            var clean = Read(source.CleanMask);
            var validation = ReferenceEquals(source.CleanMask, source.ValidationMask) ? clean : Read(source.ValidationMask);
            var known = source.KnownBoundary is null ? null : Read(source.KnownBoundary);
            var unknown = source.UnknownMask is null ? null : Read(source.UnknownMask);
            List<Wall>? walls = null;
            if (source.NativeWalls is { } array)
            {
                walls = new(array.Shape[0]);
                for (int i = 0; i < array.Shape[0]; i++)
                {
                    int offset = i * 6;
                    walls.Add(new((int)array.ReadNumericValue(offset), (int)array.ReadNumericValue(offset+1),
                        array.ReadNumericValue(offset+2), array.ReadNumericValue(offset+3),
                        array.ReadNumericValue(offset+4), checked((int)array.ReadNumericValue(offset+5))));
                }
            }
            return new(source, clean, validation, known, unknown, walls);
        }
        catch
        {
            foreach (var mat in mats) mat.Dispose();
            throw;
        }
    }

    public void Dispose()
    {
        Clean.Dispose();
        if (!ReferenceEquals(Clean, Validation)) Validation.Dispose();
        KnownBoundary?.Dispose();
        Unknown?.Dispose();
    }
}
