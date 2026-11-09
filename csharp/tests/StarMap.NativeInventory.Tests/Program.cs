using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Vision;

internal static class Program
{
    private const string SampleId = "batch02-event-000147";
    private const string HardCandidateId = "difficult-map-15";
    private const double BudgetSeconds = 30;

    private static int Main(string[] args)
    {
        try
        {
            return Run(Options.Parse(args));
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 2;
        }
    }

    private static int Run(Options options)
    {
        if (Directory.Exists(options.Output))
            throw new IOException("Use a new output directory; existing evidence is never overwritten.");
        Directory.CreateDirectory(options.Output);

        // Do the actual C# work before opening the independent Python oracle.
        // The oracle cannot feed map/pose truth or candidate ordering to Rank.
        var normal = Execute(options.Resources, options.Image, "normal", options.Floor);
        var incomplete = Execute(options.FixtureRoot, options.Image, "incomplete", options.Floor);
        var oracle = JsonNode.Parse(File.ReadAllText(options.Oracle))?.AsObject()
            ?? throw new InvalidDataException("Independent Python oracle is not a JSON object.");

        var failures = new List<string>();
        var normalRows = normal["rows"]!.AsArray();
        var incompleteRows = incomplete["rows"]!.AsArray();
        var normalHard = HardMapIds(normalRows);
        var incompleteHard = HardMapIds(incompleteRows);
        var expectedMissing = oracle["faultInjection"]?["mapId"]?.GetValue<string>()
            ?? throw new InvalidDataException("Python oracle is missing the injected competitor ID.");

        RequireEqual("oracle sample id", SampleId, Text(oracle["sample"]?["id"]), failures);
        RequireEqual("oracle current resource root", Path.GetFullPath(options.Resources),
            Path.GetFullPath(Text(oracle["resourceRoot"]) ?? ""), failures);
        RequireEqual("oracle fixture root", Path.GetFullPath(options.FixtureRoot),
            Path.GetFullPath(Text(oracle["fixtureRoot"]) ?? ""), failures);
        RequireEqual("oracle image", Path.GetFullPath(options.Image),
            Path.GetFullPath(Text(oracle["sample"]?["image"]) ?? ""), failures);
        RequireEqual("oracle image SHA-256", Text(oracle["sample"]?["imageSha256"]),
            Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(options.Image))).ToLowerInvariant(), failures);
        RequireEqual("oracle floor", options.Floor, Text(oracle["sample"]?["floor"]), failures);
        RequireEqual("oracle injected map/floor", options.Floor,
            Text(oracle["faultInjection"]?["floor"]), failures);
        RequireEqual("normal row count", 30, normalRows.Count, failures);
        RequireEqual("incomplete row count", 30, incompleteRows.Count, failures);
        RequireEqual("normal C# hard candidate", HardCandidateId,
            normalHard.Length == 1 ? normalHard[0] : string.Join(",", normalHard), failures);
        RequireSequence("normal Python/C# hard candidate", Strings(oracle["normal"]?["hardMapIds"]),
            normalHard, failures);
        RequireEqual("normal final C# hard decision", true,
            Bool(normal["decision"]?["uniqueHardIdentityCandidate"]), failures);
        RequireEqual("normal final C# selected map", HardCandidateId,
            Text(normal["decision"]?["selectedMapId"]), failures);
        RequireEqual("normal Python/C# final selected map",
            Text(oracle["normal"]?["consumer"]?["selectedMapId"]),
            Text(normal["decision"]?["selectedMapId"]), failures);

        RequireSequence("incomplete C# hard rows", [], incompleteHard, failures);
        RequireSequence("incomplete Python/C# hard rows",
            Strings(oracle["incompleteInventory"]?["hardMapIdsAfterRank"]), incompleteHard, failures);
        RequireEqual("incomplete final C# unique hard decision", false,
            Bool(incomplete["decision"]?["uniqueHardIdentityCandidate"]), failures);
        RequireEqual("incomplete final C# selected map", null,
            Text(incomplete["decision"]?["selectedMapId"]), failures);
        RequireEqual("incomplete Python/C# unique hard decision",
            Bool(oracle["incompleteInventory"]?["consumer"]?["uniqueHardIdentityCandidate"]),
            Bool(incomplete["decision"]?["uniqueHardIdentityCandidate"]), failures);
        RequireEqual("incomplete Python/C# selected map",
            Text(oracle["incompleteInventory"]?["consumer"]?["selectedMapId"]),
            Text(incomplete["decision"]?["selectedMapId"]), failures);
        RequireEqual("incomplete final C# authorizes identity", false,
            Bool(incomplete["decision"]?["authorizesIdentity"]), failures);
        RequireEqual("incomplete final C# authorizes pose", false,
            Bool(incomplete["decision"]?["authorizesPose"]), failures);
        RequireEqual("incomplete final C# requires runtime commit", true,
            Bool(incomplete["decision"]?["requiresRuntimeCommit"]), failures);

        var fixtureInventory = FindRow(incompleteRows, HardCandidateId)["nativeInventory"]?.AsObject()
            ?? throw new InvalidDataException("Incomplete C# row is missing native inventory diagnostics.");
        RequireEqual("fixture inventory complete", false, Bool(fixtureInventory["complete"]), failures);
        RequireSequence("fixture inventory missing declaration", [expectedMissing],
            Strings(fixtureInventory["missingMapIds"]), failures);
        Require(fixtureInventory["expectedMapIds"] is JsonArray expectedMaps &&
                expectedMaps.Any(node => Text(node) == expectedMissing),
            "fixture inventory expectedMapIds retains the missing declared competitor", failures);
        RequireSequence("C# fixture reader sees one unavailable target floor",
            [$"{expectedMissing}/{options.Floor}"],
            StringPairs(incomplete["unavailablePackFloors"]), failures);
        RequireSequence("normal C# catalog has no unavailable target floor", [],
            StringPairs(normal["unavailablePackFloors"]), failures);

        var pythonNormalRows = oracle["normal"]?["rows"]?.AsArray()
            ?? throw new InvalidDataException("Normal Python oracle rows are absent.");
        var pythonNegativeRows = oracle["incompleteInventory"]?["rows"]?.AsArray()
            ?? throw new InvalidDataException("Incomplete Python oracle rows are absent.");
        RequireEqual("Python oracle candidate was an actual ranked non-hard competitor", false,
            Bool(FindOracleRow(pythonNormalRows, expectedMissing)["identityHardGatePassed"]), failures);
        RequireSameOrderAndScores("C# producer leaves row order and scores unchanged",
            normalRows, incompleteRows, failures);
        RequireSameOrderAndScores("Python producer leaves row order and scores unchanged",
            pythonNormalRows, pythonNegativeRows, failures);

        foreach (var node in incompleteRows)
        {
            var row = node!.AsObject();
            RequireEqual($"{Text(row["mapId"])} hard gate suppressed", false,
                Bool(row["identityHardGatePassed"]), failures);
            RequireEqual($"{Text(row["mapId"])} suggestion suppressed", false,
                Bool(row["strongSuggestion"]), failures);
            RequireEqual($"{Text(row["mapId"])} inventory reason", "native-identity-inventory-incomplete",
                Text(row["reason"]), failures);
            bool wasPreviouslyHard = Bool(FindRow(normalRows, Text(row["mapId"])!)["identityHardGatePassed"]);
            RequireEqual($"{Text(row["mapId"])} records prior identity suppression", wasPreviouslyHard,
                Bool(row["nativeInventoryIdentitySuppressed"]), failures);
        }

        var positiveEntry = FindRow(normalRows, HardCandidateId)["difficultEntry"]?.AsObject()
            ?? throw new InvalidDataException("Normal hard candidate has no entry geometry proposal.");
        var suppressedRow = FindRow(incompleteRows, HardCandidateId);
        var suppressedEntry = suppressedRow["difficultEntry"]?.AsObject()
            ?? throw new InvalidDataException("Suppressed hard candidate has no retained entry geometry proposal.");
        RequireEqual("entry hard identity suppressed", false, Bool(suppressedEntry["identityVerified"]), failures);
        RequireEqual("entry pose verification not promoted", false, Bool(suppressedEntry["poseVerified"]), failures);
        RequireEqual("entry suppression marker", true, Bool(suppressedEntry["entryInventoryIdentitySuppressed"]), failures);
        RequireEqual("entry verification evidence retained", true,
            Bool(suppressedEntry["verification"]?["success"]), failures);
        RequireEqual("baseline verification evidence present", true,
            Bool(positiveEntry["verification"]?["success"]), failures);

        var normalMatrix = Matrix(positiveEntry["verification"]?["matrixSourceToClient"]);
        var suppressedMatrix = Matrix(suppressedEntry["verification"]?["matrixSourceToClient"]);
        var oracleEntry = FindOracleRow(oracle["incompleteInventory"]?["rows"], HardCandidateId)["difficultEntry"]?.AsObject()
            ?? throw new InvalidDataException("Python oracle is missing the preserved entry geometry.");
        var oracleNormalEntry = FindOracleRow(oracle["normal"]?["rows"], HardCandidateId)["difficultEntry"]?.AsObject()
            ?? throw new InvalidDataException("Python normal rank is missing the original entry geometry.");
        var oracleMatrix = Matrix(oracleEntry["verification"]?["matrixSourceToClient"]);
        var oracleNormalMatrix = Matrix(oracleNormalEntry["verification"]?["matrixSourceToClient"]);
        Require(normalMatrix is not null, "normal C# candidate has a geometry matrix", failures);
        Require(suppressedMatrix is not null, "suppressed C# candidate retains a geometry matrix", failures);
        RequireMatrixEqual("suppression keeps C# geometry unchanged", normalMatrix, suppressedMatrix, 1e-9, failures);
        RequireMatrixEqual("Python oracle independently retains geometry", oracleMatrix, suppressedMatrix, 1e-9, failures);
        RequireMatrixEqual("normal C# geometry matches independent Python rank", oracleNormalMatrix, normalMatrix, 1e-9, failures);

        var nativeBeforeConsume = incomplete["nativeProposal"]?.AsObject()
            ?? throw new InvalidDataException("Incomplete C# rank lost its pre-consumer native proposal.");
        var normalNative = normal["nativeProposal"]?.AsObject()
            ?? throw new InvalidDataException("Normal C# rank has no native proposal.");
        RequireEqual("native proposal status retained", Text(normalNative["status"]),
            Text(nativeBeforeConsume["status"]), failures);
        RequireEqual("native proposal candidate retained", HardCandidateId,
            Text(nativeBeforeConsume["candidateMemberId"]), failures);
        RequireEqual("native proposal hard evidence remains a proposal", true,
            Bool(nativeBeforeConsume["originalIdentityHardGatePassed"]), failures);
        RequireEqual("Python oracle native proposal candidate", HardCandidateId,
            Text(oracle["incompleteInventory"]?["nativeProposal"]?["candidateMemberId"]), failures);

        var incompleteAutomatic = Array(incomplete["decision"]?["automaticCandidates"]);
        Require(incompleteAutomatic.Count > 0, "incomplete C# consumer still returns ordinary manual candidates", failures);
        foreach (var candidate in incompleteAutomatic)
        {
            RequireEqual("automatic candidate hard identity false", false,
                Bool(candidate?["identityHardGatePassed"]), failures);
            RequireEqual("automatic candidate strong suggestion false", false,
                Bool(candidate?["strongSuggestion"]), failures);
        }
        foreach (var candidate in Array(oracle["incompleteInventory"]?["consumer"]?["automaticCandidates"]))
        {
            RequireEqual("Python automatic candidate hard identity false", false,
                Bool(candidate?["identityHardGatePassed"]), failures);
            RequireEqual("Python automatic candidate strong suggestion false", false,
                Bool(candidate?["strongSuggestion"]), failures);
        }

        var geometryComparison = new JsonObject
        {
            ["csharpNormalMatrix"] = JsonSerializer.SerializeToNode(normalMatrix),
            ["csharpIncompleteMatrix"] = JsonSerializer.SerializeToNode(suppressedMatrix),
            ["pythonIncompleteMatrix"] = JsonSerializer.SerializeToNode(oracleMatrix),
            ["pythonNormalMatrix"] = JsonSerializer.SerializeToNode(oracleNormalMatrix),
            ["csharpNormalToIncompleteMaxAbsDelta"] = MaxAbsDifference(normalMatrix, suppressedMatrix),
            ["pythonToCsharpIncompleteMaxAbsDelta"] = MaxAbsDifference(oracleMatrix, suppressedMatrix),
            ["pythonToCsharpTolerance"] = 1e-9,
        };
        var output = new JsonObject
        {
            ["schema"] = "native-inventory-s0-csharp-regression-v1",
            ["status"] = failures.Count == 0 ? "passed" : "failed",
            ["invocation"] = new JsonObject
            {
                ["resources"] = options.Resources,
                ["fixtureRoot"] = options.FixtureRoot,
                ["image"] = options.Image,
                ["oracle"] = options.Oracle,
                ["floor"] = options.Floor,
                ["sampleId"] = SampleId,
                ["rankBudgetSeconds"] = BudgetSeconds,
                ["expectedMapOrPoseSuppliedToRank"] = false,
            },
            ["normal"] = normal,
            ["incompleteInventory"] = incomplete,
            ["geometryMatrixComparison"] = geometryComparison,
            ["independentPythonOracleSummary"] = new JsonObject
            {
                ["normalHardMapIds"] = oracle["normal"]?["hardMapIds"]?.DeepClone(),
                ["normalDecision"] = oracle["normal"]?["consumer"]?.DeepClone(),
                ["injectedMapId"] = expectedMissing,
                ["incompleteHardMapIds"] = oracle["incompleteInventory"]?["hardMapIdsAfterRank"]?.DeepClone(),
                ["incompleteDecision"] = oracle["incompleteInventory"]?["consumer"]?.DeepClone(),
            },
            ["failures"] = StringsToJson(failures),
        };

        Directory.CreateDirectory(Path.GetDirectoryName(options.Output)!);
        var resultPath = Path.Combine(options.Output, "result.json");
        using (var stream = new FileStream(resultPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
            JsonSerializer.Serialize(stream, output, new JsonSerializerOptions { WriteIndented = true });
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            status = output["status"]!.GetValue<string>(),
            sampleId = SampleId,
            normalHardMapIds = normalHard,
            incompleteHardMapIds = incompleteHard,
            missingMapId = expectedMissing,
            selectedMapId = Text(incomplete["decision"]?["selectedMapId"]),
            geometryMaxDelta = MaxAbsDifference(normalMatrix, suppressedMatrix),
            result = resultPath,
            failures,
        }));
        return failures.Count == 0 ? 0 : 1;
    }

    private static JsonObject Execute(string resourceRoot, string imagePath, string label, string floor)
    {
        var resources = ResourceCatalog.Load(resourceRoot);
        var catalog = EntryPassportCatalog.Load(resourceRoot, "s0", "difficult");
        var pipeline = new S0IdentityRankPipeline(resources, catalog);
        using var frame = Cv2.ImDecode(File.ReadAllBytes(imagePath), ImreadModes.Color);
        if (frame.Empty()) throw new InvalidDataException("The selected input PNG could not be decoded.");
        var context = pipeline.Context($"native-inventory-{SampleId}-{label}", floor);
        var rank = pipeline.Rank(frame, floor, TimeSpan.FromSeconds(BudgetSeconds), context: context);
        var rows = rank.Rows;
        var nativeProposal = rank.NativeProposal;
        var decision = S0IdentityDecision.Consume(rank, null, context, SampleId + "/" + label);
        var inventory = catalog.UnavailablePackFloors
            .Where(pair => pair.Floor == floor)
            .OrderBy(pair => pair.MapId, StringComparer.Ordinal)
            .Select(pair => new JsonObject { ["mapId"] = pair.MapId, ["floor"] = pair.Floor });
        return new JsonObject
        {
            ["label"] = label,
            ["nativeResourceKey"] = pipeline.NativeResourceKey,
            ["nativeResourceError"] = pipeline.NativeResourceError,
            ["unavailablePackFloors"] = new JsonArray(inventory.Cast<JsonNode?>().ToArray()),
            ["rows"] = ProjectRows(rows),
            ["nativeProposal"] = nativeProposal,
            ["decision"] = decision.Snapshot(),
        };
    }

    private static JsonArray ProjectRows(JsonArray rows)
    {
        var result = new JsonArray();
        foreach (var node in rows)
        {
            var row = node!.AsObject();
            result.Add(new JsonObject
            {
                ["mapId"] = row["mapId"]?.DeepClone(),
                ["score"] = row["score"]?.DeepClone(),
                ["strongSuggestion"] = row["strongSuggestion"]?.DeepClone(),
                ["identityHardGatePassed"] = row["identityHardGatePassed"]?.DeepClone(),
                ["reason"] = row["reason"]?.DeepClone(),
                ["nativeInventory"] = row["nativeInventory"]?.DeepClone(),
                ["nativeInventoryIdentitySuppressed"] = row["nativeInventoryIdentitySuppressed"]?.DeepClone(),
                ["difficultEntry"] = row["difficultEntry"]?.DeepClone(),
            });
        }
        return result;
    }

    private static string[] HardMapIds(JsonArray rows) => rows
        .Where(row => Bool(row?["identityHardGatePassed"]))
        .Select(row => Text(row?["mapId"]) ?? "")
        .ToArray();

    private static void RequireSameOrderAndScores(string label, JsonArray normal, JsonArray incomplete,
        List<string> failures)
    {
        if (normal.Count != incomplete.Count)
        {
            failures.Add($"{label}: row counts differ ({normal.Count} vs {incomplete.Count})");
            return;
        }
        for (var i = 0; i < normal.Count; i++)
        {
            var left = normal[i]!.AsObject();
            var right = incomplete[i]!.AsObject();
            if (Text(left["mapId"]) != Text(right["mapId"]) ||
                Number(left["score"]) != Number(right["score"]))
            {
                failures.Add($"{label}: row {i} changed from {Text(left["mapId"])}@{Text(left["score"])} to {Text(right["mapId"])}@{Text(right["score"])}");
            }
        }
    }

    private static JsonObject FindRow(JsonArray rows, string mapId) => rows
        .Select(node => node!.AsObject())
        .Single(row => Text(row["mapId"]) == mapId);

    private static JsonObject FindOracleRow(JsonNode? rows, string mapId)
    {
        if (rows is not JsonArray array) throw new InvalidDataException("Python oracle rows are absent.");
        return array.Select(node => node!.AsObject()).Single(row => Text(row["mapId"]) == mapId);
    }

    private static JsonArray Array(JsonNode? node) => node as JsonArray ?? new JsonArray();

    private static JsonArray StringsToJson(IEnumerable<string> values)
    {
        var result = new JsonArray();
        foreach (var value in values) result.Add(value);
        return result;
    }

    private static string[] Strings(JsonNode? node) => Array(node)
        .Select(item => Text(item) ?? "")
        .ToArray();

    private static string[] StringPairs(JsonNode? node) => Array(node)
        .Select(item => $"{Text(item?["mapId"])}/{Text(item?["floor"])}")
        .ToArray();

    private static string? Text(JsonNode? node)
    {
        if (node is not JsonValue value) return null;
        try { return value.GetValue<string>(); }
        catch (InvalidOperationException) { return value.ToJsonString().Trim('"'); }
    }

    private static bool Bool(JsonNode? node)
    {
        if (node is not JsonValue value) return false;
        try { return value.GetValue<bool>(); }
        catch (InvalidOperationException) { return false; }
    }

    private static double Number(JsonNode? node) => TryNumber(node, out var number) ? number : double.NaN;

    private static double[][]? Matrix(JsonNode? node)
    {
        if (node is not JsonArray rows || rows.Count != 3) return null;
        var matrix = new double[3][];
        for (var r = 0; r < 3; r++)
        {
            if (rows[r] is not JsonArray cells || cells.Count != 3) return null;
            matrix[r] = new double[3];
            for (var c = 0; c < 3; c++)
            {
                if (!TryNumber(cells[c], out matrix[r][c])) return null;
            }
        }
        return matrix;
    }

    private static bool TryNumber(JsonNode? node, out double number)
    {
        number = 0;
        if (node is not JsonValue value) return false;
        try { number = value.GetValue<double>(); return double.IsFinite(number); }
        catch (InvalidOperationException) { return false; }
    }

    private static double? MaxAbsDifference(double[][]? left, double[][]? right)
    {
        if (left is null || right is null) return null;
        var maximum = 0d;
        for (var r = 0; r < 3; r++)
            for (var c = 0; c < 3; c++)
                maximum = Math.Max(maximum, Math.Abs(left[r][c] - right[r][c]));
        return maximum;
    }

    private static void RequireMatrixEqual(string label, double[][]? expected, double[][]? actual,
        double tolerance, List<string> failures)
    {
        if (expected is null || actual is null)
        {
            failures.Add($"{label}: a required 3x3 matrix is missing");
            return;
        }
        var difference = MaxAbsDifference(expected, actual);
        if (difference is null || difference > tolerance)
            failures.Add($"{label}: max absolute difference {difference?.ToString("R", CultureInfo.InvariantCulture) ?? "n/a"} exceeds {tolerance.ToString("R", CultureInfo.InvariantCulture)}");
    }

    private static void Require(bool condition, string message, List<string> failures)
    {
        if (!condition) failures.Add(message);
    }

    private static void RequireEqual<T>(string label, T expected, T actual, List<string> failures)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            var expectedText = expected is null ? "null" : Convert.ToString(expected, CultureInfo.InvariantCulture);
            var actualText = actual is null ? "null" : Convert.ToString(actual, CultureInfo.InvariantCulture);
            failures.Add($"{label}: expected {expectedText}, got {actualText}");
        }
    }

    private static void RequireSequence(string label, IReadOnlyList<string> expected,
        IReadOnlyList<string> actual, List<string> failures)
    {
        if (!expected.SequenceEqual(actual, StringComparer.Ordinal))
            failures.Add($"{label}: expected [{string.Join(",", expected)}], got [{string.Join(",", actual)}]");
    }

    private sealed record Options(string Resources, string FixtureRoot, string Image, string Oracle,
        string Output, string Floor)
    {
        public static Options Parse(string[] args)
        {
            var values = new Dictionary<string, string>(StringComparer.Ordinal);
            for (var i = 0; i < args.Length; i++)
            {
                if (!args[i].StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
                    throw new ArgumentException($"Expected --name value, got: {args[i]}");
                values.Add(args[i][2..], args[++i]);
            }
            string Required(string key) => values.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value)
                ? Path.GetFullPath(value)
                : throw new ArgumentException($"Missing --{key}.");
            var allowed = new HashSet<string>(["resources", "fixture-root", "image", "oracle", "output", "floor"], StringComparer.Ordinal);
            var unknown = values.Keys.Where(key => !allowed.Contains(key)).ToArray();
            if (unknown.Length != 0) throw new ArgumentException("Unknown options: " + string.Join(", ", unknown));
            return new(
                Required("resources"),
                Required("fixture-root"),
                Required("image"),
                Required("oracle"),
                Required("output"),
                values.TryGetValue("floor", out var floor) ? floor : "1");
        }
    }
}
