using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>Owned producer pixels and their exact static bridge. This is material, never live authority.</summary>
public sealed class PreparedDisplayLayer
{
    readonly byte[] pixels;
    internal PreparedDisplayLayer(DisplayBridgeResult bridge, byte[] ownedPixels)
    { Bridge = bridge; pixels = ownedPixels; }
    public DisplayBridgeResult Bridge { get; }
    public int Width => Bridge.Width;
    public int Height => Bridge.Height;
    public int Stride => checked(Width * 4);
    public byte[] CopyPixels() => (byte[])pixels.Clone();
    public bool IsCurrent() => Bridge.IsCurrent();
}

public sealed record DisplayMaterialResult(PreparedDisplayLayer? Layer, string Reason)
{
    public bool Prepared => Layer is not null;
}

/// <summary>Loads one explicitly selected display layer; author/fallback policy remains with the runtime owner.</summary>
public sealed class DisplayMaterialPreparer
{
    internal const int MaximumPixelBytes = 128 * 1024 * 1024;
    readonly DisplayBridgeResolver resolver = new(new OpenCvDisplayRasterValidator());

    public DisplayMaterialResult Prepare(DisplaySelectionResult selection, SharedStructureData? fittedReference = null,
        CancellationToken cancellation = default)
    {
        ArgumentNullException.ThrowIfNull(selection);
        cancellation.ThrowIfCancellationRequested();
        // EffectiveProfileId is policy metadata, not the selected bitmap. In
        // particular, both a base atlas and neutral fallback use shared-structure.
        return selection.Prepared && selection.Bridge is { } bridge
            ? Prepare(bridge, fittedReference, cancellation) : new(null, selection.Reason);
    }

    public DisplayMaterialResult PrepareExact(ResourceCatalog catalog, ResourceCatalogEntry entry,
        string effectiveProfile, DisplayAffine referenceToClient, string variant = "variant-01",
        SharedStructureData? fittedReference = null, CancellationToken cancellation = default)
    {
        cancellation.ThrowIfCancellationRequested();
        var bridge = resolver.Resolve(catalog, entry, effectiveProfile, referenceToClient, variant);
        return Prepare(bridge, fittedReference, cancellation);
    }

    public DisplayMaterialResult Prepare(DisplayBridgeResult bridge, SharedStructureData? fittedReference = null,
        CancellationToken cancellation = default)
    {
        ArgumentNullException.ThrowIfNull(bridge);
        cancellation.ThrowIfCancellationRequested();
        if (!bridge.Accepted) return new(null, bridge.Reason);
        try
        {
            if (!bridge.IsCurrent()) return new(null, "display-resource-changed-before-material-preparation");
            if (bridge.Reference is not { } reference || bridge.ReferenceToCanonical is null
                || string.IsNullOrEmpty(bridge.CanonicalPackId)) return new(null, "display-reference-basis-unavailable");
            // The fit may have used a cached typed resource while files were replaced.
            // A newly resolved display cannot borrow that older geometry's verdict.
            if (fittedReference is not null && !SameReference(reference, fittedReference))
                return new(null, "display-reference-differs-from-fit-reference");
            int length = PixelLength(bridge.Width, bridge.Height);
            byte[] pixels;
            if (bridge.DisplayKind == "neutral-structure")
            {
                var clean = reference.CleanMask ?? throw new InvalidDataException("display-clean-channel-unavailable");
                if (!clean.Shape.SequenceEqual(new[] { bridge.Height, bridge.Width })
                    || clean.Bytes.Length != length / 4) return new(null, "display-clean-canvas-mismatch");
                pixels = new byte[length];
                var source = clean.Bytes.Span;
                for (int i = 0; i < source.Length; i++)
                {
                    if ((i & 0x3fff) == 0) cancellation.ThrowIfCancellationRequested();
                    if (source[i] == 0) continue;
                    // client_route_preferences.prepare_display_layer's original BGRA neutral colour.
                    int p = i * 4; pixels[p] = 218; pixels[p + 1] = 228; pixels[p + 2] = 238; pixels[p + 3] = 255;
                }
            }
            else
            {
                if (bridge.ImagePath is not { } path) return new(null, "display-source-image-unavailable");
                byte[] encoded = ReadImage(bridge, path);
                cancellation.ThrowIfCancellationRequested();
                DisplayImageDecodeBudget.RequireWithinLimit(encoded, MaximumPixelBytes);
                using var decoded = Cv2.ImDecode(encoded, ImreadModes.Unchanged);
                if (decoded.Empty() || decoded.Cols != bridge.Width || decoded.Rows != bridge.Height
                    || decoded.Channels() != bridge.ImageChannels || decoded.Depth() != MatType.CV_8U)
                    return new(null, "display-original-canvas-or-format-changed");
                RequireNativeCanvas(decoded);
                using var converted = new Mat();
                Mat bgra = decoded;
                if (decoded.Type() != MatType.CV_8UC4)
                {
                    // Only sources whose original producer explicitly converts
                    // RGB/L to RGBA may opt in. Default author BGRA stays strict.
                    if (!bridge.AllowBgraFormatConversion || decoded.Channels() is not (1 or 3))
                        return new(null, "display-original-bgra-canvas-required");
                    Cv2.CvtColor(decoded, converted, decoded.Channels() == 1
                        ? ColorConversionCodes.GRAY2BGRA : ColorConversionCodes.BGR2BGRA);
                    bgra = converted;
                }
                RequireNativeCanvas(bgra);
                if (bgra.Cols != bridge.Width || bgra.Rows != bridge.Height || bgra.Type() != MatType.CV_8UC4)
                    return new(null, "display-converted-canvas-or-format-changed");
                if (bridge.BaseAtlas is { } recipe)
                {
                    if (bridge.NativeToCanonical is not { } nativeToCanonical || bridge.CanonicalToClient is not { } canonicalToClient)
                        return new(null, "display-native-basis-unavailable");
                    pixels = DisplayAtlasRenderer.Render(bgra, recipe, bridge, cancellation);
                    // The producer has consumed ImageToNative exactly once. All
                    // downstream consumers now see pixels on that native canvas.
                    bridge = bridge with
                    {
                        Width = recipe.NativeWidth, Height = recipe.NativeHeight,
                        DisplayKind = "base-map", ImageChannels = 4,
                        DisplayToCanonical = nativeToCanonical,
                        DisplayToReference = bridge.ReferenceToCanonical.Value.Inverse() * nativeToCanonical,
                        DisplayToClient = canonicalToClient * nativeToCanonical,
                        BaseAtlas = null
                    };
                }
                else pixels = CopyBgra(bgra, cancellation);
            }
            cancellation.ThrowIfCancellationRequested();
            return bridge.IsCurrent() ? new(new(bridge, pixels), "prepared") : new(null, "display-resource-changed-during-material-preparation");
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidDataException
            or ArgumentException or OverflowException or OpenCVException)
        { return new(null, "display-material-unavailable:" + error.Message); }
    }

    internal static byte[] ReadImage(DisplayBridgeResult bridge, string path)
    {
        var expected = bridge.Resources?.SingleOrDefault(s => string.Equals(s.Path, path, StringComparison.OrdinalIgnoreCase));
        if (expected is null || expected.Length < 1 || expected.Length > MaximumPixelBytes)
            throw new InvalidDataException("display-image-snapshot-unavailable-or-over-budget");
        byte[] encoded = File.ReadAllBytes(path);
        if (encoded.Length != expected.Length || Convert.ToHexStringLower(SHA256.HashData(encoded)) != expected.Sha256)
            throw new InvalidDataException("display-image-changed-during-material-read");
        return encoded;
    }

    internal static int PixelLength(int width, int height)
    {
        if (width <= 0 || height <= 0 || (long)width * height > MaximumPixelBytes / 4)
            throw new InvalidDataException("display-material-exceeds-surface-budget");
        return checked(width * height * 4);
    }

    internal static void RequireNativeCanvas(Mat image)
    {
        int width = image.Cols, height = image.Rows;
        PixelLength(width, height);
        long bytesPerPixel = image.ElemSize();
        if (bytesPerPixel <= 0 || (long)width * height > MaximumPixelBytes / bytesPerPixel)
            throw new InvalidDataException("display-material-native-surface-budget-exceeded");
    }

    internal static byte[] CopyBgra(Mat image, CancellationToken cancellation)
    {
        int width = image.Cols, rows = image.Rows;
        var result = new byte[PixelLength(width, rows)];
        int stride = checked(width * 4);
        for (int row = 0; row < rows; row++)
        { cancellation.ThrowIfCancellationRequested(); Marshal.Copy(image.Ptr(row), result, row * stride, stride); }
        return result;
    }

    static bool SameReference(SharedStructureData a, SharedStructureData b) => JsonElement.DeepEquals(a.Metadata, b.Metadata)
        && SameArray(a.CleanMask, b.CleanMask) && SameArray(a.ValidationMask, b.ValidationMask)
        && SameArray(a.KnownBoundary, b.KnownBoundary) && SameArray(a.UnknownMask, b.UnknownMask) && SameArray(a.NativeWalls, b.NativeWalls);
    static bool SameArray(NpyArray? a, NpyArray? b) => a is null ? b is null : b is not null
        && a.Dtype == b.Dtype && a.Shape.SequenceEqual(b.Shape) && a.Bytes.Span.SequenceEqual(b.Bytes.Span);
}
