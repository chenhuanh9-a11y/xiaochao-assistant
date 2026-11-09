using System.Runtime.InteropServices;
using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision;

public static class FastStructure
{
    /// <summary>Generate a distance field from the selected structure as an offline resource operation.</summary>
    public static FastPrepared Prepare(SharedStructureData reference)
    {
        if (reference.CleanMask is not { } clean)
            throw new InvalidDataException("fast-clean-mask-unavailable");
        using var raw = Geometry.FromBytes(clean.Shape[1], clean.Shape[0], clean.Bytes.ToArray());
        return Prepare(raw) with { Source = reference };
    }

    public static FastPrepared Prepare(Mat cleanMask)
    {
        if (cleanMask.Empty() || cleanMask.Type() != MatType.CV_8UC1)
            throw new ArgumentException("Expected a nonempty unsigned 8-bit clean mask.", nameof(cleanMask));
        using var mask = new Mat();
        Cv2.Threshold(cleanMask, mask, 0, 1, ThresholdTypes.Binary);
        using var edge = new Mat();
        using var kernel = Geometry.Kernel(3);
        Cv2.MorphologyEx(mask, edge, MorphTypes.Gradient, kernel);
        using var interior = new Mat();
        Cv2.Threshold(edge, interior, 0, 1, ThresholdTypes.BinaryInv);
        using var distance = new Mat();
        Cv2.DistanceTransform(interior, distance, DistanceTypes.L2, DistanceTransformMasks.Mask3);
        // Saturating uint16 conversion after scale 4 equals rint(min(distance,16383.75)*4).
        // Explicit clipping also handles OpenCV's all-nonzero distance sentinel.
        Cv2.Threshold(distance, distance, 16383.75, 0, ThresholdTypes.Trunc);
        using var quantized = new Mat();
        distance.ConvertTo(quantized, MatType.CV_16UC1, 4);
        var field = MemoryMarshal.Cast<byte, ushort>(Geometry.Bytes(quantized)).ToArray();
        var nodes = Geometry.Contour(mask);
        var chains = new List<float[][]>();
        var rays = new List<float[][]>();
        for (int i = 0; i < nodes.Count; i++)
        {
            var node = nodes[i];
            if (!node.Valid || !nodes[node.Previous].Valid || !nodes[node.Next].Valid) continue;
            chains.Add([Point(nodes[node.Previous].Point), Point(node.Point), Point(nodes[node.Next].Point)]);
            rays.Add(node.Rays.Select(Point).ToArray());
        }
        return new(mask.Cols, mask.Rows, field, chains.ToArray(), rays.ToArray(), FastAlignment.Pitch(edge));

        static float[] Point(Point2d p) => [(float)p.X, (float)p.Y];
    }

    /// <summary>Read an existing -fast.npz only when its source metadata and canvas match this reference.</summary>
    public static FastPrepared LoadPrepared(string npzPath, SharedStructureData reference)
    {
        if (reference.CleanMask is not { } clean) throw new InvalidDataException("fast-clean-mask-unavailable");
        using var archive = NpzArchive.Open(npzPath);
        using var metadata = JsonDocument.Parse(archive.ReadArray("sourceMetadata").ReadUnicodeScalar());
        if (!JsonElement.DeepEquals(reference.Metadata, metadata.RootElement))
            throw new InvalidDataException("fast-prepared-source-metadata-mismatch");
        int height = clean.Shape[0], width = clean.Shape[1];
        var shape = archive.ReadArray("shape");
        if (shape.Rank != 1 || shape.Shape[0] != 2 || shape.ReadNumericValue(0) != height || shape.ReadNumericValue(1) != width)
            throw new InvalidDataException("fast-prepared-source-shape-mismatch");
        var distance = archive.ReadArray("distance");
        if (!distance.Shape.SequenceEqual(clean.Shape) || distance.Dtype is not ("<u2" or "=u2") || !BitConverter.IsLittleEndian)
            throw new InvalidDataException("fast-prepared-distance-invalid");
        var chains = ReadVectors(archive.ReadArray("chains"), 3);
        var rays = ReadVectors(archive.ReadArray("rays"), 2);
        var pitchArray = archive.ReadArray("pitch");
        if (pitchArray.Rank != 0 || chains.Length != rays.Length)
            throw new InvalidDataException("fast-prepared-chains-or-pitch-invalid");
        double pitch = pitchArray.ReadNumericValue(0);
        if (pitch < 0 || !double.IsFinite(pitch)) throw new InvalidDataException("fast-prepared-pitch-invalid");
        return new FastPrepared(width, height, MemoryMarshal.Cast<byte, ushort>(distance.Bytes.Span).ToArray(), chains, rays, pitch)
            { Source = reference };
    }

    private static float[][][] ReadVectors(NpyArray array, int points)
    {
        if (array.Rank != 3 || array.Shape[1] != points || array.Shape[2] != 2 || array.Dtype is not ("<f4" or "=f4"))
            throw new InvalidDataException("fast-prepared-vector-shape-or-type-invalid");
        var values = MemoryMarshal.Cast<byte, float>(array.Bytes.Span);
        var result = new float[array.Shape[0]][][];
        int index = 0;
        for (int row = 0; row < result.Length; row++)
        {
            result[row] = new float[points][];
            for (int point = 0; point < points; point++)
            {
                float x = values[index++], y = values[index++];
                if (!float.IsFinite(x) || !float.IsFinite(y)) throw new InvalidDataException("fast-prepared-vector-nonfinite");
                result[row][point] = [x,y];
            }
        }
        return result;
    }

    public static FastProposalResult Propose(Mat frameBgr, FastPrepared prepared, Func<bool>? cancelled = null)
    {
        if (cancelled?.Invoke() == true)
            return new([], new() { ["reason"] = "structure-work-obsolete" }, new());
        using var observation = Geometry.Current(frameBgr);
        if (observation is null)
            return new([], new() { ["reason"] = "fast-observation-unavailable" }, new());
        return Propose(frameBgr, observation, prepared, cancelled: cancelled);
    }

    public static FastProposalResult Propose(Mat frameBgr, Observation observation, FastPrepared prepared,
        double[][]? prior = null, bool localOnly = false, Func<bool>? cancelled = null)
    {
        observation.RequireFrame(frameBgr);
        if (cancelled?.Invoke() == true)
            return new([], new() { ["reason"] = "structure-work-obsolete" }, new());
        var edges = Geometry.Observed(frameBgr, observation);
        return FastAlignment.Propose(edges, () => Geometry.ObservedChains(frameBgr, observation), prepared, prior, localOnly, cancelled);
    }
}
