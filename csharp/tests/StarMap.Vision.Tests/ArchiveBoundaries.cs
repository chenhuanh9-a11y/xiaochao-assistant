using System.Buffers.Binary;
using System.IO.Compression;
using System.Text;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Vision.Tests;

internal static class ArchiveBoundaries
{
    internal sealed record Result(bool Passed, int Chains, int Rays, int Width, int Height, string? EmptyCanvasError);

    internal static Result Run(string resourceRoot,string output,string preparedPath,ResourceCatalogEntry entry,SharedStructureData data)
    {
        var dir = Path.Combine(output,"archive-variants");
        Directory.CreateDirectory(dir);
        var blankChainsPath = Path.Combine(dir,"empty-chains-fast.npz");
        WriteVariant(preparedPath,blankChainsPath,new Dictionary<string,byte[]> {
            ["chains.npy"] = EmptyNpy("<f4","0, 3, 2"), ["rays.npy"] = EmptyNpy("<f4","0, 2, 2")
        });
        var empty = FastStructure.LoadPrepared(blankChainsPath,data);
        // Match the selected catalog context in a separate temporary pack, changing exactly the clean array.
        string modifiedRoot = Path.Combine(dir,"empty-canvas-pack");
        string typed = entry.StructureNpzPath!;
        string refDirectory = Path.GetDirectoryName(Path.GetDirectoryName(preparedPath))!;
        foreach (var file in Directory.EnumerateFiles(refDirectory,"*.json"))
            CopyManifest(file);
        string contextDir = Path.GetDirectoryName(refDirectory)!;
        foreach (var file in Directory.EnumerateFiles(contextDir,"*.json"))
            CopyManifest(file);
        string emptyPath = Path.Combine(modifiedRoot,Path.GetRelativePath(resourceRoot,typed));
        Directory.CreateDirectory(Path.GetDirectoryName(emptyPath)!);
        string cleanName = entry.StructureKind == "legacy-clean" ? "clean_mask" :
            data.Metadata.GetProperty("roles").GetProperty("clean").GetString()!;
        WriteVariant(typed,emptyPath,new Dictionary<string,byte[]> {
            [cleanName+".npy"] = EmptyNpy("|u1",$"0, {data.CleanMask!.Shape[1]}")
        });
        var catalog = ResourceCatalog.Load(modifiedRoot);
        var same = catalog.Find(entry.Mode,entry.Season,entry.Profile,entry.MapId,entry.Floor)
            ?? throw new InvalidDataException("Unable to bind mutated empty-mask resource to the same map and floor.");
        var rejected = catalog.LoadSharedStructure(same);
        bool valid = empty.Chains.Length == 0 && empty.Rays.Length == 0 && empty.Width == data.CleanMask.Shape[1]
            && empty.Height == data.CleanMask.Shape[0] && !rejected.Success && rejected.Error?.Code == "structure-mask-type-or-rank-invalid";
        return new(valid,empty.Chains.Length,empty.Rays.Length,empty.Width,empty.Height,rejected.Error?.Code);

        void CopyManifest(string original)
        {
            var target = Path.Combine(modifiedRoot,Path.GetRelativePath(resourceRoot,original));
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(original,target);
        }
    }

    private static void WriteVariant(string source,string target,IReadOnlyDictionary<string,byte[]> replacements)
    {
        using var input = ZipFile.OpenRead(source);
        using var output = ZipFile.Open(target,ZipArchiveMode.Create);
        foreach (var entry in input.Entries)
        {
            using var dest = output.CreateEntry(entry.FullName,CompressionLevel.Fastest).Open();
            if (replacements.TryGetValue(entry.FullName,out var payload)) dest.Write(payload);
            else
            {
                using var old = entry.Open();
                old.CopyTo(dest);
            }
        }
    }

    private static byte[] EmptyNpy(string descriptor,string shape)
    {
        string header = $"{{'descr': '{descriptor}', 'fortran_order': False, 'shape': ({shape}), }}";
        int padding = (16 - (10 + header.Length + 1) % 16) % 16;
        var bytes = Encoding.ASCII.GetBytes(header + new string(' ',padding) + "\n");
        var result = new byte[10+bytes.Length];
        result[0] = 0x93;
        "NUMPY"u8.CopyTo(result.AsSpan(1));
        result[6] = 1;
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(8),checked((ushort)bytes.Length));
        bytes.CopyTo(result,10);
        return result;
    }
}
