using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Runtime;
using StarMap.Vision;

internal static class Program
{
    static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    static readonly DisplayLayerSelector Selector = new(new OpenCvDisplayRasterValidator());
    static readonly DisplayMaterialPreparer Preparer = new();
    static readonly Dictionary<string, ResourceCatalog> Catalogs = new(StringComparer.OrdinalIgnoreCase);
    static readonly List<object> Failures = [];
    static string Output = "";
    static int Count;

    static int Main(string[] args)
    {
        if (args.Length == 3 && args[0] == "--budget-fixture") return BudgetFixture(args[1], args[2]);
        if (args.Length != 4)
        { Console.Error.WriteLine("Usage: StarMap.DisplayMaterial.Tests <original-229-oracle.json> <producer-cases.json> <new-evidence-dir> <truncated-icon-evidence.json> | --budget-fixture <fixture.json> <new-evidence-dir>"); return 1; }
        Output = Path.GetFullPath(args[2]);
        if (Directory.Exists(Output) && Directory.EnumerateFileSystemEntries(Output).Any())
        { Console.Error.WriteLine("Use a new empty evidence directory."); return 1; }
        Directory.CreateDirectory(Path.Combine(Output, "cases"));
        try
        {
            using var original = JsonDocument.Parse(File.ReadAllBytes(args[0]));
            using var producer = JsonDocument.Parse(File.ReadAllBytes(args[1]));
            using var truncatedIcon = JsonDocument.Parse(File.ReadAllBytes(args[3]));
            foreach (var row in original.RootElement.GetProperty("cases").EnumerateArray()) Case(row);
            foreach (var row in producer.RootElement.GetProperty("cases").EnumerateArray()) Case(row);
            var basis = producer.RootElement.GetProperty("cases").EnumerateArray().First();
            Mutation(basis, "image");
            Mutation(basis, "icon");
            ReferenceReplacement(basis);
            Boundaries(basis);
            TruncatedOptionalIcon(truncatedIcon.RootElement);
            MainImageTruncatedPng(truncatedIcon.RootElement);
            OversizedOptionalIcon(basis);
        }
        catch (Exception error) { Failures.Add(new { label = "harness", error = error.ToString() }); }
        File.WriteAllText(Path.Combine(Output, "summary.json"), JsonSerializer.Serialize(new
            { cases = Count, passed = Count - Failures.Count, failures = Failures }, JsonOptions));
        Console.WriteLine($"Display material: {Count - Failures.Count}/{Count}; evidence {Output}");
        return Failures.Count == 0 ? 0 : 1;
    }

    static int BudgetFixture(string fixturePath, string newOutput)
    {
        string output = Path.GetFullPath(newOutput);
        if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
        { Console.Error.WriteLine("Use a new empty evidence directory."); return 1; }
        Directory.CreateDirectory(output);
        try
        {
            using var fixture = JsonDocument.Parse(File.ReadAllBytes(fixturePath));
            var row = fixture.RootElement.GetProperty("selectionInput");
            var startedMemory = Memory();
            var catalog = ResourceCatalog.Load(Text(row, "resourceRoot"));
            var selection = Select(row, catalog, Entry(catalog, row));
            Require(selection.Prepared && selection.Bridge!.BaseAtlas is not null, "actual budget fixture did not select its base atlas");
            Require(string.Equals(selection.Bridge!.BaseAtlas!.ExitIconPath, Text(fixture.RootElement, "iconPath"), StringComparison.OrdinalIgnoreCase),
                "budget fixture did not use the real large encoded icon");
            // This diagnostic runs alone in a cold process. Collect the selector's
            // temporary managed buffers before measuring the native producer.
            GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
            var beforeMemory = Memory();
            long beforeManaged = GC.GetTotalAllocatedBytes(true);
            var watch = Stopwatch.StartNew();
            var result = Preparer.Prepare(selection, selection.Bridge.Reference);
            watch.Stop();
            long managedAllocationBytes = GC.GetTotalAllocatedBytes(true) - beforeManaged;
            var afterMemory = Memory();
            var evidence = new
            {
                label = Text(row, "label"), sourceFixture = Path.GetFullPath(fixturePath),
                encodedBytes = fixture.RootElement.GetProperty("encodedBytes").GetInt64(),
                sourceBgraBytes = fixture.RootElement.GetProperty("bgraBytes").GetInt64(),
                budgetBytes = fixture.RootElement.GetProperty("budgetBytes").GetInt64(),
                actualRowsReadBack = fixture.RootElement.GetProperty("actualRowsReadBack").GetInt32(),
                selection, result.Prepared, result.Reason, elapsedMs = watch.Elapsed.TotalMilliseconds,
                startedMemory, beforeMemory, afterMemory, managedAllocationBytes,
                scope = "Cold actual selector/preparer process; process memory includes native allocation and other runtime overhead, not a unit-test allocation counter"
            };
            File.WriteAllText(Path.Combine(output, "budget.json"), JsonSerializer.Serialize(evidence, JsonOptions));
            Console.WriteLine(JsonSerializer.Serialize(evidence, JsonOptions));
            Require(!result.Prepared && result.Reason.Contains("surface-budget", StringComparison.Ordinal), "large actual encoded source did not hit its surface budget");
            return 0;
        }
        catch (Exception error)
        {
            File.WriteAllText(Path.Combine(output, "failure.json"), JsonSerializer.Serialize(new { error = error.ToString() }, JsonOptions));
            Console.Error.WriteLine(error); return 1;
        }
    }

    static object Memory()
    {
        using var process = Process.GetCurrentProcess(); process.Refresh();
        return new { workingSetBytes = process.WorkingSet64, privateBytes = process.PrivateMemorySize64,
            peakWorkingSetBytes = process.PeakWorkingSet64, peakPagedBytes = process.PeakPagedMemorySize64 };
    }

    static void Check(string label, Action action)
    {
        Count++;
        try { action(); }
        catch (Exception error)
        {
            Failures.Add(new { label, error = error.ToString() });
            Console.Error.WriteLine("FAIL " + label + ": " + error.Message);
        }
    }

    static void Case(JsonElement row) => Check(Text(row, "label"), () =>
    {
        var (catalog, entry) = Context(row);
        var selection = Select(row, catalog, entry);
        var watch = Stopwatch.StartNew();
        // The complete real selector chooses the bridge. No second Resolve by
        // EffectiveProfileId and no independent easier pixel path is permitted.
        var result = Preparer.Prepare(selection, selection.Bridge?.Reference);
        watch.Stop();
        var layer = result.Layer;
        var pixels = layer?.CopyPixels();
        string? digest = pixels is null ? null : Hash(pixels);
        WriteCase(new { label = Text(row, "label"), selection, materialReason = result.Reason,
            bridge = layer?.Bridge, pixelSha256 = digest, elapsedMs = watch.Elapsed.TotalMilliseconds });
        bool prepared = row.GetProperty("prepared").GetBoolean();
        Require(selection.Prepared == prepared, "selector verdict: " + selection.Reason);
        Require(result.Prepared == prepared, "material verdict: " + result.Reason);
        if (!prepared) return;
        var expected = row.GetProperty("expected");
        var source = selection.Bridge!;
        var actual = layer!.Bridge;
        bool atlas = expected.TryGetProperty("atlas", out var recipe);
        var shape = (atlas ? recipe.GetProperty("nativeShape") : expected.GetProperty("shape"))
            .EnumerateArray().Select(v => v.GetInt32()).ToArray();
        Require(layer.Width == shape[1] && layer.Height == shape[0], "producer canvas shape");
        Require(layer.Stride == shape[1] * 4 && pixels!.Length == shape[0] * shape[1] * 4, "producer byte shape");
        Require(digest == Text(expected, "originalProducerPixelSha256"), "original complete producer pixels differ: " + digest);
        if (Optional(expected, "originalProducerPixelsPath") is { } expectedPath)
        {
            var expectedPixels = File.ReadAllBytes(expectedPath);
            Require(pixels!.AsSpan().SequenceEqual(expectedPixels), "complete producer byte comparison differs");
        }
        var expectedReference = Affine(expected.GetProperty("displayToReference"));
        var expectedClient = Affine(expected.GetProperty("displayToClient"));
        if (atlas)
        {
            var inverse = Affine(recipe.GetProperty("imageToNative")).Inverse();
            expectedReference *= inverse;
            expectedClient *= inverse;
        }
        CompareCorners(actual.DisplayToReference, expectedReference, layer.Width, layer.Height, "display/reference");
        CompareCorners(actual.DisplayToClient, expectedClient, layer.Width, layer.Height, "display/client");
        CompareCorners(actual.DisplayToCanonical, Affine(expected.GetProperty("referenceToCanonical")) * expectedReference,
            layer.Width, layer.Height, "display/canonical");
        Require(ReferenceEquals(actual.Resources, source.Resources), "original resource snapshots lost");
        Require(ReferenceEquals(actual.Reference, source.Reference), "reference identity was replaced");
        Require(actual.CanonicalPackId == source.CanonicalPackId && actual.BindingKey == source.BindingKey, "binding identity changed");
        Require(!actual.AuthorizesMapIdentity && !actual.AuthorizesStructurePose && !actual.AuthorizesCrossFloorProjection, "material grants authority");
        Require(layer.IsCurrent(), "new material is stale");
        if (source.BaseAtlas is not null)
        {
            Require(actual.DisplayToCanonical!.Value.AlmostEquals(source.NativeToCanonical!.Value), "raw image bridge survived native producer");
            Require(actual.BaseAtlas is null, "consumed recipe can be applied a second time");
            for (int i = 0; i < pixels!.Length; i += 4)
                if (pixels[i + 3] == 0) Require((pixels[i] | pixels[i + 1] | pixels[i + 2]) == 0, "transparent atlas RGB not cleared");
        }
        pixels![0] ^= 255;
        Require(Hash(layer.CopyPixels()) == digest, "returned pixel copy leaked writable owned storage");
    });

    static void Mutation(JsonElement row, string kind) => Check("same-length-replacement/" + kind, () =>
    {
        string scratch = CopyFixture(row, "replacement-" + kind);
        var catalog = ResourceCatalog.Load(Path.Combine(scratch, "_internal"));
        var entry = Entry(catalog, row);
        var selection = Select(row, catalog, entry);
        var before = Preparer.Prepare(selection, selection.Bridge!.Reference);
        Require(before.Prepared && selection.Bridge!.BaseAtlas is not null, "copied real selection not base atlas");
        string path = kind == "image" ? selection.Bridge!.ImagePath! : selection.Bridge!.BaseAtlas!.ExitIconPath!;
        var bytes = File.ReadAllBytes(path); var stamp = File.GetLastWriteTimeUtc(path);
        bytes[^1] ^= 1; File.WriteAllBytes(path, bytes); File.SetLastWriteTimeUtc(path, stamp);
        bool oldCurrent = before.Layer!.IsCurrent();
        var oldSelectionMaterial = Preparer.Prepare(selection, selection.Bridge!.Reference);
        var after = Select(row, catalog, entry);
        WriteCase(new { label = "same-length-replacement/" + kind, path, length = bytes.Length, oldCurrent,
            materialReason = oldSelectionMaterial.Reason, after });
        Require(!oldCurrent && !selection.IsCurrent(), "same-length/timestamp replacement retained old pixels");
        Require(!oldSelectionMaterial.Prepared, "stale selected snapshot was rendered again");
        if (kind == "image")
        {
            Require(after.Prepared && after.Bridge!.BaseAtlas is null, "changed art revived the old base-map producer");
            Require(Preparer.Prepare(after, after.Bridge!.Reference).Prepared, "valid neutral fallback unavailable");
        }
    });

    static void ReferenceReplacement(JsonElement row) => Check("changed-reference-does-not-borrow-old-fit", () =>
    {
        string scratch = CopyFixture(row, "replacement-reference");
        string root = Path.Combine(scratch, "_internal");
        var catalog = ResourceCatalog.Load(root); var entry = Entry(catalog, row);
        var before = Select(row, catalog, entry);
        Require(before.Prepared, "original reference not selected");
        var oldFitReference = before.Bridge!.Reference!;
        File.Copy(Text(row, "changedReferencePath"), entry.StructureNpzPath!, true);
        var freshCatalog = ResourceCatalog.Load(root);
        var after = Select(row, freshCatalog, Entry(freshCatalog, row));
        Require(after.Prepared, "new reference has no neutral display: " + after.Reason);
        var newReference = after.Bridge!.Reference!;
        Require(JsonElement.DeepEquals(oldFitReference.Metadata, newReference.Metadata), "reference negative changed metadata instead of content");
        Require(!oldFitReference.CleanMask!.Bytes.Span.SequenceEqual(newReference.CleanMask!.Bytes.Span), "replacement did not change the real clean channel");
        var rejected = Preparer.Prepare(after, oldFitReference);
        var valid = Preparer.Prepare(after, newReference);
        WriteCase(new { label = "changed-reference-does-not-borrow-old-fit", before, after, rejected.Reason, newReferencePrepared = valid.Prepared });
        Require(!rejected.Prepared && rejected.Reason == "display-reference-differs-from-fit-reference", "new pixels borrowed old fit geometry");
        Require(valid.Prepared, "matching fresh reference did not prepare: " + valid.Reason);
    });

    static void Boundaries(JsonElement row)
    {
        var (catalog, entry) = Context(row); var selection = Select(row, catalog, entry);
        Check("cancellation", () =>
        {
            using var cancel = new CancellationTokenSource(); cancel.Cancel();
            try { Preparer.Prepare(selection, cancellation: cancel.Token); throw new InvalidDataException("cancellation was ignored"); }
            catch (OperationCanceledException) { }
        });
        Check("native-canvas-budget", () =>
        {
            var bridge = selection.Bridge! with { BaseAtlas = selection.Bridge!.BaseAtlas! with { NativeWidth = 8193, NativeHeight = 8193 } };
            var result = Preparer.Prepare(bridge, bridge.Reference);
            WriteCase(new { label = "native-canvas-budget", result.Reason });
            Require(!result.Prepared && result.Reason.Contains("surface-budget"), "native allocation budget was ignored");
        });
        Check("source-canvas-budget", () =>
        {
            var result = Preparer.Prepare(selection.Bridge! with { Width = 8193, Height = 8193 });
            Require(!result.Prepared && result.Reason.Contains("surface-budget"), "source allocation budget was ignored");
        });
        Check("icon-canvas-budget", () =>
        {
            var bridge = selection.Bridge! with { BaseAtlas = selection.Bridge!.BaseAtlas! with { ExitIconSize = 8193 } };
            var result = Preparer.Prepare(bridge, bridge.Reference);
            Require(!result.Prepared && result.Reason.Contains("surface-budget"), "icon allocation budget was ignored");
        });
        Check("strict-default-bgra", () =>
        {
            // Reuse a real selected RGB producer, then remove only its explicit
            // conversion capability. The default must not infer permission.
            string producerRoot = Directory.GetParent(Directory.GetParent(Text(row, "resourceRoot"))!.FullName)!.FullName;
            string rgbRoot = Path.Combine(producerRoot, "rgb-art-gray-icon", "_internal");
            var rgbCatalog = ResourceCatalog.Load(rgbRoot); var rgb = Select(row, rgbCatalog, Entry(rgbCatalog, row));
            Require(rgb.Prepared && rgb.Bridge!.ImageChannels == 3, "actual selected RGB fixture unavailable");
            var result = Preparer.Prepare(rgb.Bridge! with { AllowBgraFormatConversion = false });
            Require(!result.Prepared && result.Reason == "display-original-bgra-canvas-required", "default strict BGRA weakened");
        });
    }

    static void TruncatedOptionalIcon(JsonElement evidence) => Check("truncated-optional-icon-matches-pillow-none", () =>
    {
        Require(Text(evidence, "schema") == "original-pillow-truncated-optional-icon-v1"
            && Text(evidence, "pillowExitIconResult") == "None", "independent original Pillow None evidence is missing");
        byte[] encoded = File.ReadAllBytes(Text(evidence, "pngPath"));
        Require(encoded.LongLength == evidence.GetProperty("pngLength").GetInt64()
            && Hash(encoded) == Text(evidence, "pngSha256"), "Pillow optional-icon fixture bytes changed");
        var header = DisplayImageDecodeBudget.TryRead(encoded);
        Require(header is { Width: 17, Height: 13, PngChunkLengthsValid: false }
            && evidence.GetProperty("idatClaimedLength").GetInt64() > encoded.Length,
            "fixture is not a small IHDR with a truncated giant IDAT chunk");

        var row = evidence.GetProperty("selectionInput");
        string resourceRoot = Text(row, "resourceRoot");
        var catalog = ResourceCatalog.Load(resourceRoot);
        var selection = Select(row, catalog, Entry(catalog, row));
        Require(selection.Prepared && selection.Bridge?.BaseAtlas is { } recipe
            && string.Equals(recipe.ExitIconPath, Text(evidence, "iconPath"), StringComparison.OrdinalIgnoreCase),
            "fixture did not select the real optional exit icon");
        var result = Preparer.Prepare(selection, selection.Bridge!.Reference);
        var pixels = result.Layer?.CopyPixels();
        string? digest = pixels is null ? null : Hash(pixels);
        WriteCase(new { label = "truncated-optional-icon-matches-pillow-none", pillow = Text(evidence, "pillow"),
            pillowExitIconResult = Text(evidence, "pillowExitIconResult"), result.Prepared, result.Reason,
            pixelSha256 = digest, expectedPixelsSha256 = Text(evidence, "expectedPixelsSha256") });
        Require(result.Prepared, "truncated optional icon was not omitted: " + result.Reason);
        byte[] expected = File.ReadAllBytes(Text(evidence, "expectedPixelsPath"));
        Require(pixels!.LongLength == evidence.GetProperty("expectedPixelsLength").GetInt64()
            && digest == Text(evidence, "expectedPixelsSha256") && pixels.AsSpan().SequenceEqual(expected),
            "bad optional icon did not preserve the original Pillow no-icon render");
    });

    static void MainImageTruncatedPng(JsonElement evidence) => Check("main-image-truncated-png-remains-fatal", () =>
    {
        var row = evidence.GetProperty("selectionInput");
        string scratch = CopyFixture(row, "main-image-truncated");
        var catalog = ResourceCatalog.Load(Path.Combine(scratch, "_internal"));
        var selection = Select(row, catalog, Entry(catalog, row));
        if (!selection.Prepared || selection.Bridge?.ImagePath is not { } imagePath)
            throw new InvalidDataException("main-image fixture did not select");
        var bridge = selection.Bridge!;
        byte[] encoded = File.ReadAllBytes(Text(evidence, "pngPath"));
        File.WriteAllBytes(imagePath, encoded);
        var snapshots = bridge.Resources!.Select(resource => string.Equals(resource.Path, imagePath, StringComparison.OrdinalIgnoreCase)
            ? resource with { Length = encoded.LongLength, Sha256 = Hash(encoded) } : resource).ToArray();
        Require(snapshots.Count(resource => string.Equals(resource.Path, imagePath, StringComparison.OrdinalIgnoreCase)) == 1,
            "main image was absent from the selected source snapshots");
        var result = Preparer.Prepare(bridge with { Resources = snapshots }, bridge.Reference);
        WriteCase(new { label = "main-image-truncated-png-remains-fatal", result.Prepared, result.Reason });
        Require(!result.Prepared && result.Reason == "display-material-unavailable:" + DisplayImageDecodeBudget.TruncatedPngReason,
            "truncated main image was swallowed as optional");
    });

    static void OversizedOptionalIcon(JsonElement row) => Check("optional-icon-surface-budget-remains-fatal", () =>
    {
        string scratch = CopyFixture(row, "oversized-optional-icon");
        string iconPath = Path.Combine(scratch, "_internal", "assets", "custom-maps", "exit-icon.png");
        File.WriteAllBytes(iconPath, PngHeader(8193, 8193));
        var catalog = ResourceCatalog.Load(Path.Combine(scratch, "_internal"));
        var selection = Select(row, catalog, Entry(catalog, row));
        Require(selection.Prepared && selection.Bridge?.BaseAtlas is not null, "oversized icon fixture was not selected");
        var result = Preparer.Prepare(selection, selection.Bridge!.Reference);
        WriteCase(new { label = "optional-icon-surface-budget-remains-fatal", result.Prepared, result.Reason });
        Require(!result.Prepared && result.Reason.Contains("surface-budget", StringComparison.Ordinal),
            "optional icon surface-budget failure was swallowed as None");
    });

    static byte[] PngHeader(uint width, uint height)
    {
        byte[] bytes = new byte[33];
        new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }.CopyTo(bytes, 0);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(8, 4), 13);
        "IHDR"u8.CopyTo(bytes.AsSpan(12, 4));
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(16, 4), width);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(20, 4), height);
        bytes[24] = 8; bytes[25] = 6;
        return bytes;
    }

    static string CopyFixture(JsonElement row, string name)
    {
        string source = Directory.GetParent(Text(row, "resourceRoot"))!.FullName;
        string destination = Path.Combine(Output, "scratch", name);
        foreach (string path in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            string target = Path.Combine(destination, Path.GetRelativePath(source, path));
            Directory.CreateDirectory(Path.GetDirectoryName(target)!); File.Copy(path, target);
        }
        return destination;
    }
    static (ResourceCatalog, ResourceCatalogEntry) Context(JsonElement row)
    {
        string root = Text(row, "resourceRoot");
        if (!Catalogs.TryGetValue(root, out var catalog)) Catalogs.Add(root, catalog = ResourceCatalog.Load(root));
        return (catalog, Entry(catalog, row));
    }
    static ResourceCatalogEntry Entry(ResourceCatalog catalog, JsonElement row)
    {
        var entries = catalog.Entries.Where(e => e.Season == Text(row, "season") && e.Mode == Text(row, "mode")
            && e.MapId == Text(row, "mapId") && e.Floor == Text(row, "floor")).ToArray();
        return entries.FirstOrDefault(e => e.Profile == "shared-structure") ?? entries.First();
    }
    static DisplaySelectionResult Select(JsonElement row, ResourceCatalog catalog, ResourceCatalogEntry entry) =>
        Selector.Select(catalog, entry, Text(row, "preferred"), Affine(row.GetProperty("referenceToClient")), Text(row, "variant"));
    static void WriteCase(object value) => File.WriteAllText(Path.Combine(Output, "cases", $"{Count:D4}.json"), JsonSerializer.Serialize(value, JsonOptions));
    static string Hash(byte[] pixels) => Convert.ToHexStringLower(SHA256.HashData(pixels));
    static string Text(JsonElement row, string key) => row.GetProperty(key).GetString()!;
    static string? Optional(JsonElement row, string key) => row.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    static DisplayAffine Affine(JsonElement value) => DisplayAffine.TryRead(value, out var result) ? result : throw new InvalidDataException("invalid oracle affine");
    static void CompareCorners(DisplayAffine? actual, DisplayAffine expected, int width, int height, string label)
    {
        Require(actual is not null, label + " absent");
        foreach (var (x, y) in new[] { (0d, 0d), ((double)width - 1, 0d), (0d, (double)height - 1), ((double)width - 1, (double)height - 1) })
        {
            var a = actual!.Value.Apply(x, y); var b = expected.Apply(x, y);
            Require(Math.Abs(a.X - b.X) < 1e-7 && Math.Abs(a.Y - b.Y) < 1e-7, label + " pixel corner differs");
        }
    }
    static void Require(bool condition, string message) { if (!condition) throw new InvalidDataException(message); }
}
