using System.Text.Json;
using StarMap.Core.Display;

namespace StarMap.Core.Resources;

/// <summary>Static renderer coordinates, never a measured scale, pose or floor permission.</summary>
public sealed record CommonFloorSpace(string SpaceId, DisplayAffine ReferenceToCommon)
{
    // Algebraically inverse to the S0 renderer/world bridge below, composed
    // in the producer's pixel units. Avoid world-unit roundtrip residue at
    // exact integer marker positions; do not round genuinely fractional data.
    internal static DisplayAffine S0MarkerToReference(SharedStructureData reference)
    {
        var meta=reference.Metadata;var geometry=meta.GetProperty("sourceGeometry");
        if(!DisplayAffine.TryRead(geometry.GetProperty("matrixRendererPixelToProduct"),out var product))
            throw new InvalidDataException("static-marker-product-bridge-unavailable");
        var bounds=geometry.GetProperty("bounds");
        var matrix=product*new DisplayAffine(1,0,-bounds.GetProperty("minX").GetDouble()*60/128,
            0,-1,-bounds.GetProperty("minY").GetDouble()*60/128);
        if(meta.TryGetProperty("calibration",out var calibration))
        {
            if(!DisplayAffine.TryRead(calibration.GetProperty("matrixCurrentReferenceToNative"),out var native))
                throw new InvalidDataException("static-marker-calibration-unavailable");
            matrix=native.Inverse()*matrix;
        }
        return matrix;
    }

    public static CommonFloorSpace? Read(ResourceCatalogEntry entry, SharedStructureData reference, out string reason)
    {
        reason = "common-space-source-unavailable";
        var meta = reference.Metadata;
        if (!meta.TryGetProperty("sourceKind", out var kind) || kind.ValueKind!=JsonValueKind.String || kind.GetString() != "game-rendered-shared-structure"
            || !meta.TryGetProperty("sourceGeometry", out var geometry) || geometry.ValueKind != JsonValueKind.Object) return null;
        try
        {
            var binding=meta.GetProperty("binding");
            if(binding.GetProperty("mapId").GetString()!=entry.MapId || binding.GetProperty("floor").GetString()!=entry.Floor
                || binding.GetProperty("profile").GetString()!=entry.Profile)return null;
            string version = meta.GetProperty("sourceVersion").GetString()!;
            if (string.IsNullOrEmpty(version)) return null;
            DisplayAffine matrix; string source;
            if (entry.Season == "s1")
            {
                source = geometry.GetProperty("sourceCatalogSha256").GetString()!;
                if (string.IsNullOrEmpty(source) || !DisplayAffine.TryRead(geometry.GetProperty("imageToMapPlaneXZ"), out matrix)
                    || !matrix.IsInvertible) return null;
            }
            else if (entry.Season == "s0")
            {
                // Difficult's compatibility map declaration can omit layout;
                // the validated native canvas carries the actual renderer ID.
                var canvas=meta.GetProperty("canvas");
                source = canvas.TryGetProperty("layout",out var nativeLayout)?nativeLayout.ToString()
                    : geometry.TryGetProperty("layout",out var layout)?layout.ToString():entry.SourceLayout??"";
                if (string.IsNullOrEmpty(source)) return null;
                if (geometry.TryGetProperty("layout", out var declared) && declared.ToString() != source) return null;
                double pitch = geometry.TryGetProperty("targetPixelsPerVoxel", out var p) ? p.GetDouble()
                    : geometry.GetProperty("cellPixels").GetDouble();
                if (pitch != 60 || !DisplayAffine.TryRead(geometry.GetProperty("matrixRendererPixelToProduct"), out var product)
                    || !product.IsInvertible) return null;
                var bounds = geometry.GetProperty("bounds");
                double x = bounds.GetProperty("minX").GetDouble(), y = bounds.GetProperty("minY").GetDouble();
                // native24/build_native_24.py's actual renderer-to-world map.
                // Product/crop changes must retain their declared static bridge.
                matrix = new DisplayAffine(128d/pitch, 0, x, 0, 128d/pitch, y) * product.Inverse();
            }
            else return null;
            if (meta.TryGetProperty("calibration", out var calibration))
            {
                if (!DisplayAffine.TryRead(calibration.GetProperty("matrixCurrentReferenceToNative"), out var native)) return null;
                matrix *= native;
            }
            if (!matrix.IsFinite || !matrix.IsInvertible) return null;
            reason = "renderer-common-space";
            return new($"{entry.Season}:{entry.Mode}:{entry.MapId}:{source}:{version}", matrix);
        }
        catch (Exception error) when (error is KeyNotFoundException or InvalidOperationException or FormatException
            or ArgumentException or InvalidDataException or OverflowException) { return null; }
    }
}
