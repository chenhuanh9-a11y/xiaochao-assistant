using System.Text;
using System.Text.Json.Nodes;
using StarMap.Core.Resources;
using StarMap.Core.Settings;

string Required(string name)
{
    int index = Array.IndexOf(args, name);
    return index >= 0 && index + 1 < args.Length ? args[index + 1] : throw new ArgumentException(name + " is required");
}
string evidence = Path.GetFullPath(Required("--output"));
if (Directory.Exists(evidence) || File.Exists(evidence)) throw new IOException("Preserve existing test output; provide a new directory.");
Directory.CreateDirectory(evidence);
var checks = new List<object>();
void Check(bool result, string name)
{
    checks.Add(new { name, passed = result });
    Console.WriteLine((result ? "ok " : "FAIL ") + name);
    if (!result) throw new InvalidDataException(name);
}

var root = new ExperimentalDataRoot(Path.Combine(evidence, "settings-copy"));
var missing = await SettingsDocument.LoadAsync(root);
Check(missing.Succeeded && missing.Document?.GameMode == "nightmare" && missing.Document.Hotkey == "G", "missing-settings-defaults");
Check(!File.Exists(root.UiSettingsPath), "read-defaults-does-not-create-player-file");
var sourcePath = Path.Combine(evidence, "original-settings.json");
byte[] original = Encoding.UTF8.GetBytes("{\"gameMode\":\"nightmare\",\"gameSeason\":\"s0\",\"hotkey\":\"G\",\"sheetOpacity\":0.55,\"futureSetting\":{\"enabled\":true}}");
await File.WriteAllBytesAsync(sourcePath, original);
var imported = await SettingsDocument.ImportAsync(root, sourcePath);
Check(imported.Succeeded && imported.Document is not null, "explicit-copy-import");
byte[] originalAfterImport = await File.ReadAllBytesAsync(sourcePath);
Check(original.AsSpan().SequenceEqual(originalAfterImport), "original-settings-unchanged");
var settings = imported.Document!;
settings.Set("sheetOpacity", .7);
var save = await settings.SaveAsync(root);
var readback = JsonNode.Parse(await File.ReadAllTextAsync(root.UiSettingsPath))!.AsObject();
Check(save.Succeeded && readback["sheetOpacity"]!.GetValue<double>() == .7 && readback["futureSetting"]!["enabled"]!.GetValue<bool>(), "save-preserves-unknown-fields");
readback["hotkey"] = "H";
await File.WriteAllTextAsync(root.UiSettingsPath, readback.ToJsonString());
byte[] externalEdit = await File.ReadAllBytesAsync(root.UiSettingsPath);
settings.Set("sheetOpacity", .8);
var conflict = await settings.SaveAsync(root);
byte[] afterConflict = await File.ReadAllBytesAsync(root.UiSettingsPath);
Check(!conflict.Succeeded && externalEdit.AsSpan().SequenceEqual(afterConflict), "external-edit-is-not-overwritten");
var repeatedImport = await SettingsDocument.ImportAsync(root, sourcePath);
byte[] afterRejectedImport = await File.ReadAllBytesAsync(root.UiSettingsPath);
Check(!repeatedImport.Succeeded && externalEdit.AsSpan().SequenceEqual(afterRejectedImport), "existing-copy-import-refused");
var brokenRoot = new ExperimentalDataRoot(Path.Combine(evidence, "broken-settings"));
Directory.CreateDirectory(brokenRoot.Path);
byte[] broken = Encoding.UTF8.GetBytes("{\"sheetOpacity\":");
await File.WriteAllBytesAsync(brokenRoot.UiSettingsPath, broken);
var brokenLoad = await SettingsDocument.LoadAsync(brokenRoot);
byte[] afterBrokenLoad = await File.ReadAllBytesAsync(brokenRoot.UiSettingsPath);
Check(!brokenLoad.Succeeded && broken.AsSpan().SequenceEqual(afterBrokenLoad), "invalid-json-retained");

foreach (JsonNode? invalid in new JsonNode?[] { null, new JsonObject(), new JsonArray(), JsonValue.Create("false"), JsonValue.Create(1) })
{
    var raw = new JsonObject { ["automaticFailureEvidence"] = invalid };
    var invalidEvidence = new SettingsDocument(raw);
    Check(!invalidEvidence.AutomaticFailureEvidence && JsonNode.DeepEquals(raw, invalidEvidence.Values), "invalid-existing-evidence-value-disabled-" + (invalid?.GetType().Name ?? "null"));
}
Check(new SettingsDocument(new JsonObject()).AutomaticFailureEvidence, "missing-evidence-value-keeps-source-default");
var freshRoot = new ExperimentalDataRoot(Path.Combine(evidence, "fresh-defaults"));
var fresh = (await SettingsDocument.LoadAsync(freshRoot)).Document!;
var freshActions = fresh.ActionHotkeys.ToArray();
fresh.Set("sheetOpacity", .6);
Check((await fresh.SaveAsync(freshRoot)).Succeeded, "first-fresh-save");
var freshReload = (await SettingsDocument.LoadAsync(freshRoot)).Document!;
Check(freshActions.SequenceEqual(fresh.ActionHotkeys) && freshActions.SequenceEqual(freshReload.ActionHotkeys)
      && freshReload.Values["actionHotkeys"] is JsonArray { Count: 10 }, "fresh-shortcuts-stable-after-first-save-and-reload");
var legacyRoot = new ExperimentalDataRoot(Path.Combine(evidence, "legacy-defaults"));
Directory.CreateDirectory(legacyRoot.Path); await File.WriteAllTextAsync(legacyRoot.UiSettingsPath, "{}");
var legacy = (await SettingsDocument.LoadAsync(legacyRoot)).Document!;
legacy.Set("sheetOpacity", .6);
Check((await legacy.SaveAsync(legacyRoot)).Succeeded && legacy.ActionHotkeys[0] == "F5" && !legacy.Values.ContainsKey("actionHotkeys"), "legacy-missing-shortcuts-not-converted");
var context = new SettingsDocument(new JsonObject { ["gameMode"] = "nightmare", ["gameSeason"] = "s0" });
context.SetMany(new Dictionary<string, JsonNode?> { ["gameMode"] = JsonValue.Create("difficulty2"), ["gameSeason"] = JsonValue.Create("s1") });
Check(context.GameMode == "difficulty2" && context.GameSeason == "s1", "batch-mode-season-independent-of-edit-order");
var beforeInvalidBatch = context.Values;
bool invalidBatchRejected = false;
try { context.SetMany(new Dictionary<string, JsonNode?> { ["gameSeason"] = JsonValue.Create("s0"), ["sheetOpacity"] = JsonValue.Create(.8) }); }
catch (ArgumentException) { invalidBatchRejected = true; }
Check(invalidBatchRejected && JsonNode.DeepEquals(beforeInvalidBatch, context.Values), "invalid-batch-does-not-partly-mutate-settings");
context.Set("sheetOpacity", .7);
Check(context.GameMode == "difficulty2" && context.SheetOpacity == .7, "retired-mode-unrelated-edit-preserved");
var keys = new SettingsDocument(new JsonObject()); keys.Set("hotkey", "control+g");
Check(keys.Hotkey == "Ctrl+G", "chord-alias-normalized");
foreach (string invalidKey in new[] { "F6", "F06", "Win+G", "not-a-key" })
{
    var before = keys.Values; bool rejected = false;
    try { keys.Set("hotkey", invalidKey); } catch (ArgumentException) { rejected = true; }
    Check(rejected && JsonNode.DeepEquals(before, keys.Values), "invalid-or-conflicting-map-key-rejected-" + invalidKey);
}
var freedActions = keys.ActionHotkeys.ToArray(); freedActions[1] = "";
keys.SetMany(new Dictionary<string, JsonNode?> { ["hotkey"] = JsonValue.Create("F06"), ["actionHotkeys"] = new JsonArray(freedActions.Select(text => (JsonNode?)JsonValue.Create(text)).ToArray()) });
Check(keys.ActionHotkeys[1] == "" && keys.Hotkey.Length != 0, "batch-frees-conflicting-action-before-map-binding");
keys.Set("hotkey", ""); Check(keys.Hotkey == "", "empty-map-key-is-explicit-unbind");
foreach (var (name, invalidTail) in new (string Name, JsonNode? Value)[]
         {
             ("object", new JsonObject()), ("null", null),
             ("invalid-text", JsonValue.Create("not-a-key")), ("invalid-type", JsonValue.Create(17))
         })
{
    var badTail = new JsonArray(Enumerable.Range(0, 9).Select(_ => (JsonNode?)JsonValue.Create("")).Append(invalidTail?.DeepClone()).ToArray());
    var preservedTail = new SettingsDocument(new JsonObject { ["hotkey"] = "G", ["actionHotkeys"] = badTail });
    preservedTail.Set("sheetOpacity", .6);
    var beforeShortcutEdit = preservedTail.Values;
    bool tailRejected = false;
    try { preservedTail.Set("hotkey", "H"); } catch (ArgumentException) { tailRejected = true; }
    Check(tailRejected && JsonNode.DeepEquals(beforeShortcutEdit, preservedTail.Values)
          && preservedTail.SheetOpacity == .6,
        "bad-legacy-tail-preserved-and-shortcut-edit-blocked-" + name);

    var beforeInvalidShortcutBatch = preservedTail.Values;
    var conflictingActions = Enumerable.Range(0, 10).Select(_ => (JsonNode?)JsonValue.Create("")).ToArray();
    conflictingActions[0] = JsonValue.Create("F1");
    bool batchRejected = false;
    try
    {
        preservedTail.SetMany(new Dictionary<string, JsonNode?>
        {
            ["hotkey"] = JsonValue.Create("F1"),
            ["actionHotkeys"] = new JsonArray(conflictingActions)
        });
    }
    catch (ArgumentException) { batchRejected = true; }
    Check(batchRejected && JsonNode.DeepEquals(beforeInvalidShortcutBatch, preservedTail.Values),
        "invalid-explicit-hotkey-batch-does-not-repair-tail-partially-" + name);

    var unboundActions = Enumerable.Range(0, 10).Select(_ => (JsonNode?)JsonValue.Create("")).ToArray();
    preservedTail.SetMany(new Dictionary<string, JsonNode?>
    {
        ["actionHotkeys"] = new JsonArray(unboundActions)
    });
    Check(preservedTail.Values["actionHotkeys"] is JsonArray { Count: 10 } repairedActions
          && repairedActions[9] is JsonValue repairedTail && repairedTail.GetValue<string>() == ""
          && preservedTail.ActionHotkeys[9] == "",
        "explicit-empty-picker-tail-repairs-legacy-" + name);
}
var invalidGallerySource = Path.Combine(evidence, "invalid-gallery.json");
await File.WriteAllTextAsync(invalidGallerySource, "{\"schema\":\"xingtu-custom-gallery-v1\",\"galleries\":[{\"id\":\"fixture\",\"name\":\"Fixture\",\"maps\":{\"nightmare-03\":{\"points\":[{}]}}}]}");
var invalidGalleryRoot = new ExperimentalDataRoot(Path.Combine(evidence, "invalid-gallery-copy"));
var badGallery = await CustomGalleriesDocument.ImportAsync(invalidGalleryRoot, invalidGallerySource);
Check(!badGallery.Succeeded && !File.Exists(invalidGalleryRoot.CustomGalleriesPath), "invalid-route-point-refused-before-copy-adoption");

await SettingsFieldSaveRegression.Run(evidence, Check);

var catalog = ResourceCatalog.Load(Required("--resources"));
Check(catalog.Entries.Count > 0, "actual-resource-index-entries");
var candidates = catalog.Entries.Where(entry => entry.StructureNpzPath is not null)
    .GroupBy(entry => new { entry.Mode, entry.Season, entry.Floor, entry.StructureKind })
    .Select(group => group.First()).ToArray();
Check(candidates.Length > 0, "actual-bound-structure-available");
var resourceEvidence = new List<object>();
foreach (var entry in candidates)
{
    var result = catalog.LoadSharedStructure(entry);
    Check(result.Success && result.Value is not null, "actual-structure-load-" + entry.StableKey);
    var data = result.Value!;
    var mask = data.CleanMask ?? data.ValidationMask;
    Check(mask.Rank == 2 && data.ValidationMask.Shape.SequenceEqual(mask.Shape), "mask-shape-" + entry.StableKey);
    resourceEvidence.Add(new { entry.StableKey, entry.Mode, entry.Season, entry.Profile, entry.MapId, entry.Floor, entry.StructureKind, entry.StructureNpzPath, cleanAvailable = data.CleanMask is not null, shape = mask.Shape, dtype = mask.Dtype, nonzero = mask.Bytes.ToArray().Count(b => b != 0), validationNonzero = data.ValidationMask.Bytes.ToArray().Count(b => b != 0) });
}
Check(catalog.Find("nightmare", "s0", "missing-profile", "missing-map", "B999") is null, "missing-binding-not-synthesized");
var report = new { checks, catalogEntries = catalog.Entries.Count, catalogIssues = catalog.Issues.Count, resources = resourceEvidence, scope = "Actual resource and explicit experimental settings copy; no game/input/recognition/installation acceptance" };
await File.WriteAllTextAsync(Path.Combine(evidence, "result.json"), System.Text.Json.JsonSerializer.Serialize(report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine("migration-foundation-ok");
