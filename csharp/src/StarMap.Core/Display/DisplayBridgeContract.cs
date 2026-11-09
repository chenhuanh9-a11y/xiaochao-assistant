using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

public sealed record DisplayRasterInfo(int Width, int Height, int Channels, bool HasVisiblePixels, bool? FootprintMatches = null);
public sealed record DisplayAtlasEntrance(string Label,double ImageX,double ImageY,int NativeLeft,int NativeTop);
public sealed record DisplayBaseAtlasRecipe(DisplayAffine ImageToNative,int NativeWidth,int NativeHeight,bool ArtIncludesExits,
    string? ExitIconPath,int ExitIconSize,IReadOnlyList<DisplayAtlasEntrance> Entrances);

/// <summary>Decode real bytes, with an optional exact binary alpha-footprint comparison.</summary>
public interface IDisplayRasterValidator
{
    DisplayRasterInfo Inspect(ReadOnlyMemory<byte> bytes, ReadOnlyMemory<byte>? cleanMask = null, int maskWidth = 0, int maskHeight = 0);
    NativeCornerValidation ValidateNativeCorners(NativeCornerValidationRequest request);
}

/// <summary>All paths and masks are selected by the caller's trusted resource context, never by evidence pairs.</summary>
public sealed record NativeCornerValidationRequest(string Profile, string MapId, string Floor, string Variant,
    string NativeLayout, int NativeFloor, string DefaultProfile, string CanonicalProfile,
    string AuthorPath, string NativeMaskPath, int NativeWidth, int NativeHeight, long NativeBytes,
    string NativeRelativePath, DisplayAffine PrimaryProfileToCanonical, DisplayAffine PrimaryProfileToNative,
    DisplayAffine CandidateProfileToCanonical, JsonElement Evidence);

public sealed record NativeCornerValidation(bool Accepted, IReadOnlyList<string> Reasons, int SupportCount = 0,
    int HoldoutCount = 0, double? SupportP90 = null, double? HoldoutP90 = null);

public sealed record DisplayResourceSnapshot(string Path, long Length, string Sha256)
{
    public bool RequiresRegularSingleLink { get; init; }
    public bool IsCurrent()
    {
        try
        {
            if (RequiresRegularSingleLink) DisplayFileAccess.RequireRegular(Path);
            var file = new FileInfo(Path);
            return file.Exists && file.Length == Length
                && string.Equals(Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(Path))), Sha256, StringComparison.Ordinal);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or InvalidDataException) { return false; }
    }
}

/// <summary>Display-only result. This type grants no identity, floor or pose authority.</summary>
public sealed record DisplayBridgeResult(bool Accepted, string Reason, string BindingKey, string Profile,
    string? ImagePath = null, string? DisplayKind = null, DisplayAffine? NativeToCanonical = null,
    DisplayAffine? DisplayToCanonical = null, DisplayAffine? DisplayToReference = null,
    DisplayAffine? DisplayToClient = null, DisplayAffine? CanonicalToClient = null,
    IReadOnlyList<DisplayResourceSnapshot>? Resources = null, int Width = 0, int Height = 0)
{
    public string? CanonicalPackId { get; init; }
    public DisplayAffine? ReferenceToCanonical { get; init; }
    [JsonIgnore] public SharedStructureData? Reference { get; init; }
    public int ImageChannels { get; init; } = 4;
    public bool AllowBgraFormatConversion { get; init; }
    public DisplayBaseAtlasRecipe? BaseAtlas { get; init; }
    public bool AuthorizesMapIdentity => false;
    public bool AuthorizesStructurePose => false;
    public bool AuthorizesCrossFloorProjection => false;
    public bool IsCurrent() => Accepted && Resources is { Count: > 0 } && Resources.All(r => r.IsCurrent());
}
