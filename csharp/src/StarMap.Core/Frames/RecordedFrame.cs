namespace StarMap.Core.Frames;

using System.Text.Json;
using System.Text.Json.Serialization;

/// <summary>
/// On-disk recorded frame for the phase-1 slice. The edge mask is the
/// observation; the uint16 field is the prepared distance <c>_grid</c> looks
/// up. This loader does not run <c>prepare()</c> and does not call OpenCV.
/// Logs and exceptions omit the filesystem path.
/// </summary>
public sealed class RecordedFrame
{
    public const string Format = "xingtu-recorded-frame-v1";
    public const int MaxDimension = 8192;

    public required string Id { get; init; }
    public required int EdgeWidth { get; init; }
    public required int EdgeHeight { get; init; }
    public required int PreparedWidth { get; init; }
    public required int PreparedHeight { get; init; }
    public required double Scale { get; init; }
    public double? PriorX { get; init; }
    public double? PriorY { get; init; }
    public required byte[] Edge { get; init; }
    public required ushort[] Distance { get; init; }

    public static RecordedFrame Load(string directory)
    {
        try
        {
            return LoadUnchecked(directory);
        }
        catch (InvalidOperationException)
        {
            throw;
        }
        catch (Exception)
        {
            throw new InvalidOperationException("recorded-frame-unreadable");
        }
    }

    private static RecordedFrame LoadUnchecked(string directory)
    {
        var manifestPath = Path.Combine(directory, "manifest.json");
        var edgePath = Path.Combine(directory, "edge.u8");
        var distancePath = Path.Combine(directory, "distance.u16");
        FrameManifest manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<FrameManifest>(File.ReadAllBytes(manifestPath), JsonOptions)
                ?? throw new InvalidOperationException("recorded-frame-manifest");
        }
        catch (JsonException)
        {
            throw new InvalidOperationException("recorded-frame-manifest");
        }
        if (manifest.Format != Format || string.IsNullOrWhiteSpace(manifest.Id))
            throw new InvalidOperationException("recorded-frame-manifest");
        if (manifest.Synthetic != true)
            throw new InvalidOperationException("recorded-frame-not-marked-synthetic");
        ValidateDimension(manifest.EdgeWidth);
        ValidateDimension(manifest.EdgeHeight);
        ValidateDimension(manifest.PreparedWidth);
        ValidateDimension(manifest.PreparedHeight);
        if (!double.IsFinite(manifest.Scale) || manifest.Scale == 0)
            throw new InvalidOperationException("recorded-frame-scale");
        bool prior = manifest.Prior is not null;
        if (prior && (!double.IsFinite(manifest.Prior!.X) || !double.IsFinite(manifest.Prior.Y)))
            throw new InvalidOperationException("recorded-frame-prior");

        var edge = File.ReadAllBytes(edgePath);
        if (edge.LongLength != (long)manifest.EdgeWidth * manifest.EdgeHeight)
            throw new InvalidOperationException("recorded-frame-edge-length");
        var distanceBytes = File.ReadAllBytes(distancePath);
        long expected = (long)manifest.PreparedWidth * manifest.PreparedHeight * 2;
        if (distanceBytes.LongLength != expected)
            throw new InvalidOperationException("recorded-frame-distance-length");
        var distance = new ushort[distanceBytes.Length / 2];
        Buffer.BlockCopy(distanceBytes, 0, distance, 0, distanceBytes.Length);
        if (!BitConverter.IsLittleEndian)
            throw new InvalidOperationException("recorded-frame-endian");

        return new RecordedFrame
        {
            Id = manifest.Id,
            EdgeWidth = manifest.EdgeWidth,
            EdgeHeight = manifest.EdgeHeight,
            PreparedWidth = manifest.PreparedWidth,
            PreparedHeight = manifest.PreparedHeight,
            Scale = manifest.Scale,
            PriorX = prior ? manifest.Prior!.X : null,
            PriorY = prior ? manifest.Prior!.Y : null,
            Edge = edge,
            Distance = distance,
        };
    }

    private static void ValidateDimension(int value)
    {
        if (value < 1 || value > MaxDimension)
            throw new InvalidOperationException("recorded-frame-dimension");
    }

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Disallow,
        AllowTrailingCommas = false,
    };

    private sealed class FrameManifest
    {
        public string? Format { get; set; }
        public string? Id { get; set; }
        public bool? Synthetic { get; set; }
        public int EdgeWidth { get; set; }
        public int EdgeHeight { get; set; }
        public int PreparedWidth { get; set; }
        public int PreparedHeight { get; set; }
        public double Scale { get; set; }
        public PriorManifest? Prior { get; set; }
    }

    private sealed class PriorManifest
    {
        [JsonPropertyName("x")]
        public double X { get; set; }
        [JsonPropertyName("y")]
        public double Y { get; set; }
    }
}
