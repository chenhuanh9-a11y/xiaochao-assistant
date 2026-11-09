using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Identity;

if (args.Length != 2)
{
    Console.Error.WriteLine("usage: StarMap.GroupChoice.Tests ORIGINAL_ORACLE NEW_OUTPUT");
    return 2;
}

var oraclePath = Path.GetFullPath(args[0]);
var outputPath = Path.GetFullPath(args[1]);
if (File.Exists(outputPath) || Directory.Exists(outputPath)) throw new IOException("Preserve previous evidence; supply a new output file.");
var oracle = JsonNode.Parse(File.ReadAllText(oraclePath))?.AsObject()
    ?? throw new InvalidDataException("oracle-root-invalid");
var cases = oracle["cases"]?.AsArray() ?? throw new InvalidDataException("oracle-cases-missing");
var results = new JsonArray();
var mismatches = 0;

foreach (var item in cases)
{
    var testCase = item?.AsObject() ?? throw new InvalidDataException("oracle-case-invalid");
    var state = ReadState(testCase["state"]?.AsObject() ?? throw new InvalidDataException("state-missing"));
    var requestNode = testCase["request"]?.AsObject() ?? throw new InvalidDataException("request-missing");
    var request = ReadRequest(Text(testCase, "action"), requestNode);
    var plan = GroupMemberChoicePlanner.Plan(state, request);
    var currentState = testCase["current_state"] is JsonObject current
        ? ReadState(current)
        : state;
    var actual = PlanJson(plan);
    var actualCurrent = GroupMemberChoicePlanner.IsCurrent(plan, currentState);
    var matchesPlan = JsonNode.DeepEquals(actual, testCase["expected"]);
    var matchesCurrent = actualCurrent == Bool(testCase, "expected_current");
    if (!matchesPlan || !matchesCurrent) mismatches++;
    results.Add(new JsonObject
    {
        ["name"] = Text(testCase, "name"),
        ["matches_plan"] = matchesPlan,
        ["matches_current"] = matchesCurrent,
        ["actual"] = actual,
        ["actual_current"] = actualCurrent,
        ["expected"] = testCase["expected"]?.DeepClone(),
        ["expected_current"] = Bool(testCase, "expected_current")
    });
}

var selfChecks = RunSelfChecks();
var report = new JsonObject
{
    ["schema"] = "group-choice-csharp-replay-v1",
    ["scope"] = "planner output and group/floor currentness only",
    ["case_count"] = cases.Count,
    ["mismatch_count"] = mismatches,
    ["all_match"] = mismatches == 0,
    ["self_checks"] = JsonSerializer.SerializeToNode(selfChecks),
    ["self_checks_passed"] = selfChecks.All(pair => pair.Value),
    ["cases"] = results
};

var outputDirectory = Path.GetDirectoryName(outputPath);
if (!string.IsNullOrEmpty(outputDirectory)) Directory.CreateDirectory(outputDirectory);
File.WriteAllText(outputPath, report.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"cases={cases.Count} mismatches={mismatches} selfChecks={selfChecks.Count(pair => pair.Value)}/{selfChecks.Count}");
Console.WriteLine($"result={outputPath}");
return mismatches == 0 && selfChecks.All(pair => pair.Value) ? 0 : 1;

static GroupMemberChoiceState ReadState(JsonObject json) => new(
    Text(json, "mode"),
    Text(json, "groupId"),
    Long(json, "groupGeneration") ?? 0,
    Long(json, "cycleId"),
    Text(json, "floor"),
    Strings(json, "groupMemberIds"),
    Text(json, "currentGroupMemberId"),
    Text(json, "provisionalMapId"),
    Text(json, "durableMapId"),
    Text(json, "mapId"),
    Bool(json, "manualGroupMemberHeld"),
    Text(json, "manualGroupMemberId"),
    Bool(json, "roundActive"),
    Text(json, "identityPhase"),
    Bool(json, "resourceLocked"),
    Bool(json, "contextPending"),
    Bool(json, "roundPreparing"),
    Bool(json, "groupMemberSelectionPending"));

static GroupMemberChoiceRequest ReadRequest(string? action, JsonObject json) => new(
    action,
    Text(json, "mapId"),
    Text(json, "groupId"),
    Long(json, "groupGeneration"),
    Long(json, "cycleId"),
    Text(json, "floor"),
    json.ContainsKey("groupId"),
    json.ContainsKey("groupGeneration"),
    json.ContainsKey("cycleId"),
    json.ContainsKey("floor"));

static JsonObject PlanJson(GroupMemberChoicePlan plan)
{
    var result = new JsonObject
    {
        ["accepted"] = plan.Accepted,
        ["changed"] = plan.Changed,
        ["reason"] = plan.Reason,
        ["message"] = plan.Message,
        ["map_id"] = plan.MapId,
        ["hold"] = plan.Hold
    };
    if (plan.Scope is null)
    {
        result["scope"] = null;
        return result;
    }

    result["scope"] = new JsonObject
    {
        ["mode"] = plan.Scope.Mode,
        ["group_id"] = plan.Scope.GroupId,
        ["group_generation"] = plan.Scope.GroupGeneration,
        ["cycle_id"] = plan.Scope.CycleId,
        ["floor"] = plan.Scope.Floor,
        ["members"] = new JsonArray(plan.Scope.Members.Select(value => (JsonNode?)JsonValue.Create(value)).ToArray()),
        ["current_member_id"] = plan.Scope.CurrentMemberId,
        ["manual_hold"] = plan.Scope.ManualHold,
        ["manual_member_id"] = plan.Scope.ManualMemberId
    };
    return result;
}

static Dictionary<string, bool> RunSelfChecks()
{
    var sourceMembers = new List<string?> { "map-a", "map-b", "map-a", "" };
    var state = new GroupMemberChoiceState("nightmare", "group-ab", 8, 4, "1", sourceMembers,
        currentGroupMemberId: "map-a", roundActive: true, identityPhase: "GROUP_LOCKED");
    sourceMembers[0] = "mutated-after-construction";
    var request = new GroupMemberChoiceRequest("select_group_member", "map-b", "group-ab", 8, 4, "1");
    var plan = GroupMemberChoicePlanner.Plan(state, request);
    var copiedMembers = state.GroupMemberIds.SequenceEqual(new[] { "map-a", "map-b" });
    var immutableMemberView = ThrowsNotSupported(() => ((IList<string>)state.GroupMemberIds)[0] = "changed");
    var scopeMemberView = ThrowsNotSupported(() => ((IList<string>)plan.Scope!.Members)[0] = "changed");
    var sameScope = GroupMemberChoicePlanner.Plan(new GroupMemberChoiceState("nightmare", "group-ab", 8, 4,
        "1", new[] { "map-a", "map-b" }, currentGroupMemberId: "map-a", roundActive: true,
        identityPhase: "GROUP_LOCKED"), request).Scope;
    var scopeValueEquality = plan.Scope!.Equals(sameScope);
    var reorderedScope = GroupMemberChoicePlanner.Plan(new GroupMemberChoiceState("nightmare", "group-ab", 8, 4,
        "1", new[] { "map-b", "map-a" }, currentGroupMemberId: "map-a", roundActive: true,
        identityPhase: "GROUP_LOCKED"), request).Scope;
    var scopeOrderIsSignificant = !plan.Scope.Equals(reorderedScope);
    var inputAndPlanStable = state.GroupMemberIds.SequenceEqual(new[] { "map-a", "map-b" })
        && plan.Hold && request.MapId == "map-b";
    var changedCopy = plan with { Hold = false };
    var planImmutable = plan.Hold && !changedCopy.Hold;
    return new Dictionary<string, bool>
    {
        ["state-copies-and-deduplicates-members"] = copiedMembers,
        ["state-members-are-read-only"] = immutableMemberView,
        ["scope-members-are-read-only"] = scopeMemberView,
        ["scope-equality-compares-member-values"] = scopeValueEquality,
        ["scope-equality-preserves-member-order"] = scopeOrderIsSignificant,
        ["inputs-and-plan-stay-unchanged"] = inputAndPlanStable,
        ["plan-with-copy-does-not-mutate-original"] = planImmutable
    };
}

static bool ThrowsNotSupported(Action action)
{
    try { action(); return false; }
    catch (NotSupportedException) { return true; }
}

static string? Text(JsonObject json, string name) => json.TryGetPropertyValue(name, out var node)
    ? node?.GetValue<string>()
    : null;

static long? Long(JsonObject json, string name) => json.TryGetPropertyValue(name, out var node)
    ? node is null ? null : node.GetValue<long>()
    : null;

static bool Bool(JsonObject json, string name) => json.TryGetPropertyValue(name, out var node)
    && node is not null && node.GetValue<bool>();

static IEnumerable<string?> Strings(JsonObject json, string name) => json[name] is JsonArray values
    ? values.Select(value => value?.GetValue<string>())
    : [];
