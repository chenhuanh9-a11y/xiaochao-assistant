using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;
using static ReplayJson;

internal static class Program
{
    private static readonly string[] PathFields = ["passportSelected", "fallbackAllowed", "passportUnresolved", "sideProducer", "sideWinnerMapId"];
    private static readonly string[] GateFields = ["automaticGateDetection", "reason", "states", "applied", "anchorCount", "evidence", "anchor", "registeredCount", "selectedCount", "selectedMapIds", "includedDisconnectedEntrance", "observationOrigin"];
    private static readonly string[] FitFields = ["success", "reason", "coordinateSpace", "routeProfileId", "strategy", "inliers", "markerOccludedWallObservation", "observationBounds", "observationComponent", "difficultEntryValidation", "localFloorDiagnostics", "entryReverification", "entryFallback"];
    private static readonly string[] PriorityIds = ["batch01-event-000006", "batch02-event-000053", "batch02-event-000097", "batch02-event-000147"];

    private static int Main(string[] args)
    {
        try { return Run(Options.Parse(args)); }
        catch (Exception error) { Console.Error.WriteLine(error); return 2; }
    }

    private static int Run(Options options)
    {
        if (Directory.Exists(options.Output) && Directory.EnumerateFileSystemEntries(options.Output).Any())
            throw new IOException("Use a new or empty output directory; original evidence is never overwritten.");
        var index = Read(Path.Combine(options.Oracle, "all-samples.json")).AsObject();
        var all = index["samples"]!.AsArray().Select(node => node!.AsObject()).ToArray();
        if (all.Length != index["count"]!.GetValue<int>() || all.Select(row => Text(row, "id")).Distinct(StringComparer.Ordinal).Count() != all.Length)
            throw new InvalidDataException("Input index count/unique sample IDs are inconsistent.");
        var s0 = all.Where(row => Text(row, "season") == "s0").ToArray();
        var selected = s0.Where(row => options.Ids is null || options.Ids.Contains(Text(row, "id"))).ToArray();
        if (selected.Length == 0 || options.Ids is not null && !selected.Select(row => Text(row, "id")).ToHashSet(StringComparer.Ordinal).SetEquals(options.Ids))
            throw new InvalidDataException("Requested S0 IDs are absent; refusing silent sample omission.");
        Directory.CreateDirectory(options.Output);
        var resources = ResourceCatalog.Load(options.Resources);
        var pipelines = new Dictionary<string, S0IdentityRankPipeline>(StringComparer.Ordinal);
        var catalogs = new Dictionary<string, EntryPassportCatalog>(StringComparer.Ordinal);
        using var hosts = new HostPool(resources);
        var cases = new List<JsonObject>();
        var identityContractsModes = new HashSet<string>(StringComparer.Ordinal);
        bool callerContractsRun = false;
        Save(Path.Combine(options.Output, "invocation.json"), new
        {
            options.Resources, options.Oracle, options.IdentityOracle, options.FitOracle, options.SupplementalFits, options.CallerOracle, options.Stage, options.BudgetSeconds, options.HostFastAlignmentEnabled,
            inventoryCount = all.Length, s0InventoryCount = s0.Length, selectedCount = selected.Length,
            selection = options.Ids is null ? "all-S0-default" : "explicit-subset", selectedIds = selected.Select(row => Text(row, "id")),
            expectedMapOrPoseSuppliedToSolver = false, input = "Original full-client PNG; fresh S0 season/mode FloorObservation",
            sourceCommit = ReadGitHead(), resourceEntries = resources.Entries.Count, resourceIssues = resources.Issues,
            timing = "Recorded only; never a numerical oracle or latency acceptance assertion"
        });

        foreach (var sample in selected)
        {
            string id = Text(sample, "id"), mode = Text(sample, "mode");
            if (Path.GetFileName(id) != id || id is "." or "..") throw new InvalidDataException("Invalid sample ID.");
            string destination = Path.Combine(options.Output, id); Directory.CreateDirectory(destination);
            var differences = new List<JsonObject>(); var coverage = new List<JsonObject>();
            var timer = Stopwatch.StartNew(); JsonObject? actual = null, callerContracts = null; string? executionError = null;
            try
            {
                // No expected status, observation, rank rows or fits are read
                // before both actual stages finish for this frame.
                using var frame = Cv2.ImDecode(File.ReadAllBytes(Text(sample, "image")), ImreadModes.Color);
                var proof = CaptureProof.Validate(sample, frame); Save(Path.Combine(destination, "capture-proof.json"), proof);
                var observed = FloorObservation.Observe(frame, "s0", mode);
                var observation = new JsonObject { ["mapObservation"] = observed.MapObservation.DeepClone(), ["floorId"] = observed.FloorId, ["floorRead"] = observed.FloorRead.DeepClone() };
                bool admitted = observed.MapOpen == true && observed.FloorId is not null;
                actual = new JsonObject
                {
                    ["id"] = id, ["season"] = "s0", ["mode"] = mode,
                    ["stageA"] = new JsonObject { ["admitted"] = admitted, ["observation"] = observation },
                    ["stageB"] = new JsonObject { ["requested"] = options.Stage == "AB", ["candidates"] = new JsonArray() }
                };
                if (admitted)
                {
                    if (!pipelines.TryGetValue(mode, out var pipeline))
                    {
                        var registered = EntryPassportCatalog.Load(options.Resources, "s0", mode);
                        catalogs.Add(mode, registered);
                        pipelines.Add(mode, pipeline = new S0IdentityRankPipeline(resources, registered));
                    }
                    var actualCatalog = catalogs[mode];
                    var rankTimer = Stopwatch.StartNew();
                    var identityContext=pipeline.Context("recorded-"+id,observed.FloorId!);
                    var completeRank = pipeline.Rank(frame, observed.FloorId!, TimeSpan.FromSeconds(options.BudgetSeconds), context: identityContext);
                    var decision=S0IdentityDecision.Consume(completeRank,null,identityContext,id);
                    var strategy = completeRank.Strategy;
                    var stageA = actual["stageA"]!.AsObject();
                    stageA["passport"] = Result(strategy.Passport);
                    stageA["finalRows"] = completeRank.Rows; stageA["evidence"] = completeRank.Evidence; stageA["decision"] = decision.Snapshot();
                    stageA["nativeResourceError"] = pipeline.NativeResourceError;
                    if(!identityContractsModes.Contains(mode) &&
                        (completeRank.NativeProposal["conditionObservations"] is JsonArray {Count:>0} || Flag(completeRank.NativeProposal,"originalIdentityHardGatePassed")))
                    {
                        var contracts=S0IdentityContracts.Solve(completeRank,decision,identityContext,id);
                        stageA["identityContracts"]=contracts; S0IdentityContracts.Require(contracts,differences); identityContractsModes.Add(mode);
                    }
                    stageA["mainRows"] = strategy.Main?.Rows.DeepClone() ?? new JsonArray();
                    stageA["mainDiagnostics"] = strategy.Diagnostics["mainDiagnostics"]?.DeepClone();
                    stageA["mainCorners"] = strategy.Main?.Corners.DeepClone();
                    stageA["side"] = strategy.Side is null ? null : Result(strategy.Side);
                    stageA["consumer"] = strategy.SideWinner?.DeepClone();
                    stageA["acceptedMapIds"] = Strings(ConsumerMapIds(strategy));
                    stageA["diagnostics"] = strategy.Diagnostics.DeepClone(); stageA["frontGate"] = strategy.FrontGate.DeepClone();
                    stageA["elapsedMs"] = rankTimer.Elapsed.TotalMilliseconds; stageA["budgetSeconds"] = options.BudgetSeconds;
                    var proposals = CompetitiveProposals(strategy, observed.FloorId!);
                    stageA["competitiveProposals"] = new JsonArray(proposals.Select(row => row.DeepClone()).ToArray());
                    if (options.Stage == "AB")
                    {
                        var fits = actual["stageB"]!["candidates"]!.AsArray();
                        ResourceCatalogEntry? callerTarget = null; JsonObject? callerBaseline = null;
                        foreach (var proposal in proposals)
                        {
                            string map = Text(proposal, "mapId"); var fitTimer = Stopwatch.StartNew();
                            JsonObject fit; string? fitError = null; S0HostFitResult? hostResult = null;
                            // Match LiveRuntime.Fit: the same registered catalog
                            // that generated A owns this map's identity profile.
                            // ResourceCatalog.Find is an exact typed lookup; a
                            // shared-structure default would skip real S0 data.
                            bool registeredProfile = actualCatalog.MapProfiles.TryGetValue(map, out string? identityProfile);
                            var target = registeredProfile ? resources.Find(actualCatalog.Mode, actualCatalog.Season,
                                identityProfile!, map, observed.FloorId!) : null;
                            try
                            {
                                // Preserve the first A producer's actual entry
                                // candidate, matching the original setdefault
                                // proposal order. No expected pose enters Host.
                                var entryCandidate = proposal["origins"]!.AsArray()[0]!["candidate"]!.DeepClone().AsObject();
                                fit = !registeredProfile ? new JsonObject { ["success"] = false, ["reason"] = "entry-catalog-map-profile-unavailable" }
                                    : target is null ? new JsonObject { ["success"] = false, ["reason"] = "target-floor-resource-unavailable" }
                                    : (hostResult = hosts.Get(actualCatalog).Fit(frame, target, entryCandidate,
                                        new S0HostFitOptions { FastAlignmentEnabled = options.HostFastAlignmentEnabled }, cancelled: () => false)).Fit;
                            }
                            catch (Exception error)
                            {
                                fitError = error.ToString();
                                fit = new JsonObject { ["success"] = false, ["reason"] = "fit-execution-error", ["error"] = error.ToString() };
                            }
                            fits.Add(new JsonObject
                            {
                                ["mapId"] = map, ["floor"] = observed.FloorId, ["proposal"] = proposal.DeepClone(),
                                ["resourceLookupMode"] = actualCatalog.Mode, ["resourceLookupSeason"] = actualCatalog.Season,
                                ["registeredIdentityProfile"] = identityProfile, ["profileSource"] = "same-stage-A-EntryPassportCatalog.MapProfiles",
                                ["resourceKey"] = target?.StableKey, ["resourceProfile"] = target?.Profile,
                                ["fitInvoked"] = target is not null,
                                ["entryCandidateSuppliedToHost"] = true, ["expectedPoseSuppliedToFit"] = false,
                                ["observationDomain"] = "original-S0-implicit-client-ROI",
                                ["canonicalPackId"] = hostResult?.CanonicalPackId, ["referenceConsumed"] = hostResult?.Reference is not null,
                                ["s0HostStages"] = hostResult?.Stages.DeepClone(), ["s0HostEvents"] = hostResult?.Events.DeepClone(),
                                ["fit"] = fit, ["supported"] = StructureEvidence.VerifiedPose(fit), ["error"] = fitError,
                                ["elapsedMs"] = fitTimer.Elapsed.TotalMilliseconds
                            });
                            if (!callerContractsRun && options.CallerOracle is not null && target is not null && Flag(fit, "success") && fit["matrixSourceToClient"] is not null)
                            {
                                callerTarget ??= target; callerBaseline ??= fit;
                            }
                        }
                        if (!callerContractsRun && callerTarget is not null && callerBaseline is not null)
                        {
                            callerContractsRun = true;
                            try { callerContracts = S0CallerContracts.Solve(resources, actualCatalog, frame, callerTarget, callerBaseline); }
                            catch (Exception error)
                            {
                                callerContracts = new JsonObject { ["executionError"] = error.ToString() };
                                Difference(differences, "/callerContracts/execution", "caller-contract-execution-error", null, JsonValue.Create(error.ToString()));
                            }
                        }
                        actual["stageB"]!["scope"] = "S0HostFit implicit-domain cold production caller with actual A entry candidate; original replay has no group/steady/prior state";
                    }
                }
                actual["elapsedMs"] = timer.Elapsed.TotalMilliseconds;
                Save(Path.Combine(destination, "actual.json"), actual);
                if (callerContracts is not null) Save(Path.Combine(destination, "caller-contracts-actual.json"), callerContracts);
            }
            catch (Exception error)
            {
                executionError = error.ToString();
                Save(Path.Combine(destination, "actual-error.json"), new { id, error = executionError, partialActual = actual, elapsedMs = timer.Elapsed.TotalMilliseconds });
                Difference(differences, "/execution", "execution-error", null, JsonValue.Create(executionError));
            }

            // Only now load independent original answers. Neither the original
            // map, floor, matrix nor the user's map confirmation enters solving.
            JsonObject? expectedStatus = null;
            try
            {
                expectedStatus = Read(Path.Combine(options.Oracle, id, "status.json")).AsObject();
                CompareExpected(options, id, actual, differences, coverage, destination);
                if (callerContracts is not null && callerContracts["executionError"] is null) S0CallerContracts.CompareOriginal(options.CallerOracle!, callerContracts, differences);
            }
            catch (Exception error)
            {
                Difference(differences, "/oracle", "oracle-read-or-comparison-error", null, JsonValue.Create(error.ToString()));
            }
            var status = new JsonObject
            {
                ["id"] = id, ["season"] = "s0", ["mode"] = mode, ["executionError"] = executionError,
                ["stageAAdmitted"] = actual?["stageA"]?["admitted"]?.DeepClone(),
                ["floor"] = actual?["stageA"]?["observation"]?["floorId"]?.DeepClone(),
                ["mapOpen"] = actual?["stageA"]?["observation"]?["mapObservation"]?["mapOpen"]?.DeepClone(),
                ["originalAdmitted"] = expectedStatus is null ? null : JsonValue.Create(Flag(expectedStatus, "mapOpen") && expectedStatus["floor"] is not null),
                ["originalStage"] = expectedStatus?["stage"]?.DeepClone(),
                ["fitAttempts"] = actual?["stageB"]?["candidates"]?.AsArray().Count ?? 0,
                ["fitExecutionErrors"] = actual?["stageB"]?["candidates"]?.AsArray().Count(row => row?["error"] is not null) ?? 0,
                ["stageADifferences"] = differences.Count(row => Text(row, "path").StartsWith("/stageA/", StringComparison.Ordinal)),
                ["stageBDifferences"] = differences.Count(row => Text(row, "path").StartsWith("/stageB/", StringComparison.Ordinal)),
                ["differenceCount"] = differences.Count, ["coverageGapCount"] = coverage.Count,
                ["observationalParityPassed"] = differences.Count == 0,
                ["passed"] = differences.Count == 0 && coverage.Count == 0,
                ["elapsedMs"] = timer.Elapsed.TotalMilliseconds
            };
            Save(Path.Combine(destination, "differences.json"), differences); Save(Path.Combine(destination, "coverage-gaps.json"), coverage);
            Save(Path.Combine(destination, "status.json"), status); cases.Add(status);
            Save(Path.Combine(options.Output, "progress.json"), new { completed = cases.Count, selected = selected.Length, latest = status });
            Console.WriteLine(JsonSerializer.Serialize(status));
        }
        int errors = cases.Count(row => row["executionError"] is not null) + cases.Sum(row => row["fitExecutionErrors"]!.GetValue<int>());
        int differenceCount = cases.Sum(row => row["differenceCount"]!.GetValue<int>()), gaps = cases.Sum(row => row["coverageGapCount"]!.GetValue<int>());
        var summary = new
        {
            passed = differenceCount == 0 && errors == 0 && gaps == 0 && (options.CallerOracle is null || callerContractsRun),
            observationalParityPassed = differenceCount == 0 && errors == 0 && (options.CallerOracle is null || callerContractsRun),
            selectedRecordedPipelineCoverageComplete = gaps == 0 && options.Stage == "AB",
            inventoryCount = all.Length, s0InventoryCount = s0.Length, selectedCount = selected.Length, resultCount = cases.Count,
            originalAdmittedCount = cases.Count(row => Flag(row, "originalAdmitted")),
            originalUnresolvedCount = cases.Count(row => row["originalAdmitted"] is JsonValue value && value.TryGetValue<bool>(out bool admitted) && !admitted),
            originalAdmissionUnavailableCount = cases.Count(row => row["originalAdmitted"] is null),
            actualAdmittedCount = cases.Count(row => Flag(row, "stageAAdmitted")), actualUnresolvedCount = cases.Count(row => !Flag(row, "stageAAdmitted") && row["executionError"] is null),
            errors, differenceCount, coverageGapCount = gaps, callerContractsRequested = options.CallerOracle is not null, callerContractsExecuted = callerContractsRun,
            fitAttempts = cases.Sum(row => row["fitAttempts"]!.GetValue<int>()), cases,
            identityContractsModes = identityContractsModes.Order(StringComparer.Ordinal),
            numericTolerance = "A abs 1e-9 / relative 1e-12; B matrix abs 1e-5; B residual abs 1e-6; discrete fields exact; no timing comparison",
            scope = "Saved original full-client S0 replay. Actual complete S0 rank/native-state/confirmed-registry consumer and A-derived S0 cold Host fits. Stateful fit services fail closed when requested but unavailable. No GUI/game/live lock or independent pose acceptance."
        };
        Save(Path.Combine(options.Output, "summary.json"), summary); Console.WriteLine(JsonSerializer.Serialize(summary));
        return summary.passed ? 0 : 1;
    }

    private static JsonObject Result(SideEntryResult result) => new() { ["rows"] = result.Rows.DeepClone(), ["diagnostics"] = result.Diagnostics.DeepClone(), ["turns"] = result.Turns.DeepClone() };
    private static string[] ConsumerMapIds(EntryStrategyResult strategy)
    {
        var accepted = (strategy.Main?.Rows ?? new JsonArray()).Where(row => Flag(row, "identityVerified")).Select(row => row!["mapId"]!.GetValue<string>()).ToList();
        if (strategy.SideWinner is { } winner) accepted.Add(Text(winner, "mapId"));
        return accepted.Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
    }

    private static List<JsonObject> CompetitiveProposals(EntryStrategyResult strategy, string floor)
    {
        var proposals = new List<JsonObject>(); var byMap = new Dictionary<string, JsonObject>(StringComparer.Ordinal);
        Add(strategy.Main?.Rows ?? new JsonArray(), "main", true);
        Add(strategy.Side?.Rows ?? new JsonArray(), "side", false);
        if (strategy.SideWinner is { } winner) Include(winner, "consumer", ["consumer-winner"]);
        return proposals;
        void Add(JsonArray rows, string source, bool main)
        {
            double top = rows.Count == 0 ? 0 : rows.Max(row => Number(row?["score"]));
            double threshold = main ? Math.Max(10, top * .65) : Math.Max(4, top - 1);
            foreach (var node in rows)
            {
                var row = node!.AsObject(); var reasons = new List<string>();
                if (Number(row["score"]) >= threshold) reasons.Add("original-competitive-band");
                if (Flag(row, "identityVerified")) reasons.Add("identity-verified");
                if (Flag(row["verification"], "success") && Flag(row["verification"]?["residual"], "verified")) reasons.Add("current-frame-verified");
                if (reasons.Count > 0) Include(row, source, reasons, threshold);
            }
        }
        void Include(JsonObject row, string source, IEnumerable<string> reasons, double? threshold = null)
        {
            string map = Text(row, "mapId");
            var origin = new JsonObject { ["source"] = source, ["threshold"] = threshold, ["selectionReasons"] = Strings(reasons), ["candidate"] = row.DeepClone() };
            if (byMap.TryGetValue(map, out var existing)) { existing["origins"]!.AsArray().Add(origin); return; }
            var proposal = new JsonObject { ["mapId"] = map, ["floor"] = floor, ["origins"] = new JsonArray(origin) };
            proposals.Add(proposal); byMap.Add(map, proposal);
        }
    }

    private static void CompareExpected(Options options, string id, JsonObject? actual, List<JsonObject> differences, List<JsonObject> coverage, string destination)
    {
        string original = Path.Combine(options.Oracle, id);
        var expectedObservation = Read(Path.Combine(original, "observation.json")).AsObject();
        Compare("/stageA/observation", expectedObservation, actual?["stageA"]?["observation"], differences);
        bool expectedAdmitted = Flag(expectedObservation["mapObservation"], "mapOpen") && expectedObservation["floorId"] is not null;
        bool actualAdmitted = Flag(actual?["stageA"], "admitted");
        Compare("/stageA/admitted", JsonValue.Create(expectedAdmitted), actual?["stageA"]?["admitted"], differences);
        if (!expectedAdmitted && !actualAdmitted) return;
        if (!expectedAdmitted)
        {
            Difference(differences, "/stageA/strategy", "unexpected-admission-with-no-original-rank", null, actual?["stageA"]);
            if (options.Stage == "AB") Difference(differences, "/stageB/candidates", "unexpected-admission-with-no-original-fit", null, actual?["stageB"]?["candidates"]);
            return;
        }
        var expectedRecognition = Read(Path.Combine(original, "recognition.json")).AsObject();
        var evidence = expectedRecognition["evidence"]?.AsObject() ?? new JsonObject();
        var a = actual?["stageA"];
        Compare("/stageA/passport/rows", evidence["entryPassportCandidates"] ?? new JsonArray(), a?["passport"]?["rows"], differences);
        Compare("/stageA/passport/diagnostics", evidence["entryPassport"], a?["passport"]?["diagnostics"], differences);
        Compare("/stageA/mainRows", evidence["tilemapCandidates"] ?? new JsonArray(), a?["mainRows"], differences);
        Compare("/stageA/mainDiagnostics", evidence["tilemapEntry"], a?["mainDiagnostics"], differences);
        Compare("/stageA/side/rows", evidence["tilemapSideCandidates"] ?? new JsonArray(), a?["side"]?["rows"] ?? (actualAdmitted ? new JsonArray() : null), differences);
        Compare("/stageA/side/diagnostics", evidence["tilemapSide"], a?["side"]?["diagnostics"], differences);
        Compare("/stageA/recognitionPath", Project(evidence["recognitionPath"]?.AsObject() ?? new(), PathFields), a?["diagnostics"], differences);
        var ranked = expectedRecognition["rows"]?.AsArray() ?? new JsonArray();
        var expectedAccepted = ranked.Where(row => Flag(row, "identityHardGatePassed")).Select(row => row!["mapId"]!.GetValue<string>()).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal);
        Compare("/stageA/consumer/acceptedMapIds", Strings(expectedAccepted), a?["acceptedMapIds"], differences);
        var expectedGate = ranked.Select(row => row?["difficultRecognition"]?["frontGateObservation"]).FirstOrDefault(node => node is JsonObject)?.AsObject();
        if (expectedGate is not null) Compare("/stageA/frontGate", Project(expectedGate, GateFields), a?["frontGate"], differences);
        Compare("/stageA/finalRows", NormalizeNative(ranked), NormalizeNative(a?["finalRows"]), differences);
        Compare("/stageA/evidence", evidence, a?["evidence"], differences);
        Compare("/stageA/decision", NormalizeNative(Read(Path.Combine(options.IdentityOracle,id,"decision.json"))), NormalizeNative(a?["decision"]), differences);
        if(a?["nativeResourceError"] is not null || a?["evidence"]?["sourceStructureCandidates"]?["reason"]?.ToString()=="source-feature-implementation-unavailable")
            coverage.Add(new JsonObject { ["stage"]="A",["status"]="actual-capability-unavailable",["nativeResourceError"]=a?["nativeResourceError"]?.DeepClone(),["sourceEvidence"]=a?["evidence"]?["sourceStructureCandidates"]?.DeepClone() });
        if (options.Stage == "A")
        {
            coverage.Add(new JsonObject { ["stage"] = "B", ["status"] = "not-requested", ["reason"] = "explicit-stage-A-only" }); return;
        }
        var expectedFits = Read(Path.Combine(options.FitOracle, id, "downstream-fit.json")).AsObject();
        var originalFits = expectedFits["candidates"]!.DeepClone().AsArray();
        int originalFitCount = originalFits.Count;
        if (options.SupplementalFits is not null)
        {
            string supplementPath = Path.Combine(options.SupplementalFits, id, "additional-downstream-fit.json");
            if (File.Exists(supplementPath))
            {
                var supplement = Read(supplementPath).AsObject();
                Compare("/stageB/supplement/fastAlignmentEnabled", supplement["fastAlignmentEnabled"], JsonValue.Create(options.HostFastAlignmentEnabled), differences);
                var proof = Read(Path.Combine(destination, "capture-proof.json"));
                foreach (var node in supplement["candidates"]!.AsArray())
                {
                    string map = node!["mapId"]!.GetValue<string>();
                    if (originalFits.Any(row => row?["mapId"]?.GetValue<string>() == map))
                        throw new InvalidDataException("Supplement must not override an original fit: " + map);
                    var input = supplement["inputs"]![map]!;
                    var actualProposal = a?["competitiveProposals"]?.AsArray().FirstOrDefault(row => row?["mapId"]?.GetValue<string>() == map);
                    Compare("/stageB/supplement/" + map + "/floor", input["floor"], a?["observation"]?["floorId"], differences);
                    Compare("/stageB/supplement/" + map + "/candidate", input["candidate"], actualProposal?["origins"]?[0]?["candidate"], differences);
                    foreach (string field in new[] { "captureRecord", "image", "capturedAt" })
                        Compare("/stageB/supplement/" + map + "/" + field, input[field], proof[field], differences);
                    originalFits.Add(node.DeepClone());
                }
                Save(Path.Combine(destination, "fit-oracle-merge.json"), new
                {
                    originalPath = Path.Combine(options.FitOracle, id, "downstream-fit.json"), originalFitCount,
                    supplementPath, supplementalFitCount = originalFits.Count - originalFitCount,
                    mergedMapIds = originalFits.Select(row => row!["mapId"]!.GetValue<string>()),
                    policy = "Append independent original Host answers after actual solving; no original overwrite, candidate deletion, changed tolerance or array relaxation"
                });
            }
        }
        var actualFits = actual?["stageB"]?["candidates"]?.AsArray() ?? new JsonArray();
        Compare("/stageB/proposalMapIds", Strings(originalFits.Select(row => row!["mapId"]!.GetValue<string>())), Strings(actualFits.Select(row => row!["mapId"]!.GetValue<string>())), differences);
        var originalSupported = originalFits.Where(row => Flag(row?["fit"], "success")).Select(row => row!["mapId"]!.GetValue<string>()).Order(StringComparer.Ordinal);
        var actualSupported = actualFits.Where(row => Flag(row?["fit"], "success")).Select(row => row!["mapId"]!.GetValue<string>()).Order(StringComparer.Ordinal);
        Compare("/stageB/supportedMapIds", Strings(originalSupported), Strings(actualSupported), differences);
        var originalVerified = originalFits.Where(row => StructureEvidence.VerifiedPose(row?["fit"])).Select(row => row!["mapId"]!.GetValue<string>()).Order(StringComparer.Ordinal);
        var actualVerified = actualFits.Where(row => StructureEvidence.VerifiedPose(row?["fit"])).Select(row => row!["mapId"]!.GetValue<string>()).Order(StringComparer.Ordinal);
        Compare("/stageB/residualVerifiedMapIds", Strings(originalVerified), Strings(actualVerified), differences);
        foreach (var node in originalFits)
        {
            string map = node!["mapId"]!.GetValue<string>(); var expectedFit = node["fit"]!.AsObject();
            var fit = actualFits.FirstOrDefault(row => row?["mapId"]?.GetValue<string>() == map)?["fit"]?.AsObject();
            if (fit is null) { Difference(differences, "/stageB/fit/" + map, "missing-candidate-fit", expectedFit, null); continue; }
            Compare("/stageB/fit/" + map, Project(expectedFit, FitFields), fit, differences);
            if (expectedFit.ContainsKey("matrixSourceToClient")) Compare("/stageB/fit/" + map + "/matrixSourceToClient", expectedFit["matrixSourceToClient"], fit["matrixSourceToClient"], differences, 1e-5);
            if (expectedFit.ContainsKey("selectedRouteCurrentFrameResidual")) Compare("/stageB/fit/" + map + "/selectedRouteCurrentFrameResidual", expectedFit["selectedRouteCurrentFrameResidual"], fit["selectedRouteCurrentFrameResidual"], differences, 1e-6);
            if (!Flag(expectedFit, "success")) Compare("/stageB/fit/" + map + "/failureDetails", expectedFit, fit, differences);
        }
        Save(Path.Combine(destination, "original-fit-paths.json"), new
        {
            originalScope = expectedFits["scope"]?.DeepClone(), originalEvents = expectedFits["events"]?.DeepClone(),
            comparedFitFields = FitFields, matrix = "all elements", residual = "all original fields including wall axes, verification and canonical provenance",
            recordedButNotCompared = "Fast proposal timing/intermediate diagnostics and Host events are retained for causal investigation; numeric result and canonical provenance are compared independently."
        });
        // The independent replay constructed a fresh _AlignmentHost(engine)
        // with no group, steady or pose priors. Its actual cold caller is now
        // exercised above. Stateful runtime integration remains a separate
        // untested consumer, not a fictitious missing stage on these inputs.
        if (id == "batch01-event-000006")
            Save(Path.Combine(destination, "posthoc-user-confirmation.json"), new
            {
                mapId = "nightmare-12", label = "A1", source = "User confirmation after capture/replay",
                compatibleScreenshotMaps = new[] { "nightmare-04", "nightmare-12", "nightmare-19" },
                suppliedToRankOrFit = false, scope = "This frame only; compatibility is not identity or permission to lock the batch"
            });
    }

    private static string? ReadGitHead()
    {
        // Metadata only: no process/build invocation. A shared checkout may
        // contain intentional uncommitted production changes.
        string? directory = AppContext.BaseDirectory;
        while (directory is not null)
        {
            string dotgit = Path.Combine(directory, ".git");
            if (Directory.Exists(dotgit)) return Head(dotgit);
            if (File.Exists(dotgit))
            {
                string pointer = File.ReadAllText(dotgit).Trim();
                if (pointer.StartsWith("gitdir: ", StringComparison.Ordinal)) return Head(Path.GetFullPath(Path.Combine(directory, pointer[8..])));
            }
            directory = Path.GetDirectoryName(directory);
        }
        return null;
        static string? Head(string git)
        {
            string file = Path.Combine(git, "HEAD"); if (!File.Exists(file)) return null;
            string value = File.ReadAllText(file).Trim(); if (!value.StartsWith("ref: ", StringComparison.Ordinal)) return value;
            string reference = Path.Combine(git, value[5..].Replace('/', Path.DirectorySeparatorChar));
            return File.Exists(reference) ? File.ReadAllText(reference).Trim() : value;
        }
    }

    private static JsonNode? NormalizeNative(JsonNode? source)
    {
        var copy=source?.DeepClone(); Walk(copy); return copy;
        static void Walk(JsonNode? node)
        {
            if(node is JsonObject row)
            {
                if(row["conditionObservations"] is JsonArray observations)
                {
                    foreach(var observed in observations) if(observed?["pairs"] is JsonArray pairs)
                        observed!["pairs"]=new JsonArray(pairs.OrderBy(pair=>pair?["poseA"]?.ToString(),StringComparer.Ordinal).ThenBy(pair=>pair?["poseB"]?.ToString(),StringComparer.Ordinal).ThenBy(pair=>pair?["viewerPoseId"]?.ToString(),StringComparer.Ordinal).Select(pair=>pair!.DeepClone()).ToArray());
                    row["conditionObservations"]=new JsonArray(observations.OrderBy(observed=>observed?["domainId"]?.ToString(),StringComparer.Ordinal).ThenBy(observed=>observed?["conditionId"]?.ToString(),StringComparer.Ordinal).Select(observed=>observed!.DeepClone()).ToArray());
                }
                foreach(var value in row.Select(pair=>pair.Value).ToArray()) Walk(value);
            }
            else if(node is JsonArray array) foreach(var value in array) Walk(value);
        }
    }

    private sealed class HostPool(ResourceCatalog resources) : IDisposable
    {
        private readonly Dictionary<EntryPassportCatalog, S0HostFit> hosts = new();
        internal S0HostFit Get(EntryPassportCatalog catalog)
        {
            if (!hosts.TryGetValue(catalog, out var host)) hosts.Add(catalog, host = new S0HostFit(resources, catalog));
            return host;
        }
        public void Dispose() { foreach (var host in hosts.Values) host.Dispose(); hosts.Clear(); }
    }

    private sealed record Options(string Resources, string Oracle, string Output, string Stage, double BudgetSeconds, HashSet<string>? Ids,
        bool HostFastAlignmentEnabled, string FitOracle, string? CallerOracle, string? SupplementalFits, string IdentityOracle)
    {
        internal static Options Parse(string[] args)
        {
            var values = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < args.Length; i += 2)
            {
                if (i + 1 == args.Length || !args[i].StartsWith("--", StringComparison.Ordinal) || !values.TryAdd(args[i], args[i + 1]))
                    throw new ArgumentException("Use --resources ROOT --oracle ORIGINAL_REPLAY_DIR --output NEW_DIR [--stage A|AB] [--budget-seconds 30] [--ids ID,ID or priority4] [--host-fast enabled|disabled --fit-oracle MATCHING_ORIGINAL_FITS]. Default: all S0, both stages.");
            }
            foreach (string key in values.Keys) if (key is not ("--resources" or "--oracle" or "--output" or "--stage" or "--budget-seconds" or "--ids" or "--host-fast" or "--fit-oracle" or "--caller-oracle" or "--supplemental-fits" or "--identity-oracle")) throw new ArgumentException("Unknown option: " + key);
            string Required(string key) => values.TryGetValue(key, out string? value) ? Path.GetFullPath(value) : throw new ArgumentException("Missing " + key);
            string stage = values.GetValueOrDefault("--stage", "AB").ToUpperInvariant();
            if (stage is not ("A" or "AB")) throw new ArgumentException("Stage must be A or AB.");
            double budget = double.Parse(values.GetValueOrDefault("--budget-seconds", "30"), System.Globalization.CultureInfo.InvariantCulture);
            if (!double.IsFinite(budget) || budget <= 0) throw new ArgumentException("Budget must be finite and positive; original replay used 30 seconds.");
            HashSet<string>? ids = values.TryGetValue("--ids", out string? text)
                ? (text == "priority4" ? PriorityIds : text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)).ToHashSet(StringComparer.Ordinal) : null;
            string fast = values.GetValueOrDefault("--host-fast", "enabled");
            if (fast is not ("enabled" or "disabled")) throw new ArgumentException("Host fast must be enabled or disabled.");
            string oracle = Required("--oracle");
            return new(Required("--resources"), oracle, Required("--output"), stage, budget, ids,
                fast == "enabled", values.TryGetValue("--fit-oracle", out string? fitOracle) ? Path.GetFullPath(fitOracle) : oracle,
                values.TryGetValue("--caller-oracle", out string? callerOracle) ? Path.GetFullPath(callerOracle) : null,
                values.TryGetValue("--supplemental-fits", out string? supplementalFits) ? Path.GetFullPath(supplementalFits) : null,
                values.TryGetValue("--identity-oracle",out string? identityOracle)?Path.GetFullPath(identityOracle):Path.Combine(Path.GetDirectoryName(oracle)!,"s0-identity-consumer-python-01"));
        }
    }
}
