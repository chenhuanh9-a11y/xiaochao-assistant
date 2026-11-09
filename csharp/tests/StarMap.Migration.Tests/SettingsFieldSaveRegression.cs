using System.Reflection;
using System.Text.Json.Nodes;
using StarMap.Core.Settings;

internal static class SettingsFieldSaveRegression
{
    public static async Task Run(string evidence, Action<bool, string> check)
    {
        const string visibility = "sheetVisibilityEnabled";
        var output = Path.Combine(evidence, "field-save");
        Directory.CreateDirectory(output);

        async Task<(ExperimentalDataRoot Root, SettingsDocument Document)> Load(string name, JsonObject? raw = null)
        {
            var root = new ExperimentalDataRoot(Path.Combine(output, name));
            if (raw is not null)
            {
                Directory.CreateDirectory(root.Path);
                await File.WriteAllTextAsync(root.UiSettingsPath, raw.ToJsonString());
            }
            var loaded = await SettingsDocument.LoadAsync(root);
            check(loaded.Succeeded, "field-save-load-" + name);
            return (root, loaded.Document!);
        }

        static async Task<JsonObject> Read(ExperimentalDataRoot root)
            => JsonNode.Parse(await File.ReadAllBytesAsync(root.UiSettingsPath))!.AsObject();

        static SemaphoreSlim Gate(SettingsDocument document)
            => (SemaphoreSlim)typeof(SettingsDocument).GetField("_saveGate", BindingFlags.NonPublic | BindingFlags.Instance)!
                .GetValue(document)!;

        var (emptyRoot, empty) = await Load("no-op");
        var unchanged = await empty.SaveFieldsAsync(emptyRoot, [visibility]);
        empty.Set(visibility, true);
        var explicitDefault = await empty.SaveFieldsAsync(emptyRoot, [visibility]);
        var emptyKeys = await empty.SaveFieldsAsync(emptyRoot, []);
        var unknownKey = await empty.SaveFieldsAsync(emptyRoot, ["futureField"]);
        check(!unchanged.Succeeded && unchanged.Error is null && !explicitDefault.Succeeded && explicitDefault.Error is null
              && !emptyKeys.Succeeded && emptyKeys.Error is ArgumentException
              && !unknownKey.Succeeded && unknownKey.Error is ArgumentException
              && !File.Exists(emptyRoot.UiSettingsPath), "field-save-no-op-and-unsupported-selection-do-not-create-file");

        var original = new JsonObject
        {
            [visibility] = true,
            ["sheetOpacity"] = "malformed opacity",
            ["hotkey"] = "not-a-key",
            ["actionHotkeys"] = new JsonObject { ["malformed"] = true },
            ["gameMode"] = "future-mode",
            ["futureField"] = new JsonObject { ["array"] = new JsonArray(1, null, "opaque") }
        };
        var (rawRoot, raw) = await Load("raw-preservation", original);
        raw.Set("sheetOpacity", .73);
        raw.Set(visibility, false);
        check((await raw.SaveFieldsAsync(rawRoot, [visibility, visibility])).Succeeded, "field-save-selected-change-succeeds");
        var rawSaved = await Read(rawRoot);
        var expectedRaw = (JsonObject)original.DeepClone();
        expectedRaw[visibility] = false;
        check(JsonNode.DeepEquals(rawSaved, expectedRaw) && raw.SheetOpacity == .73
              && raw.Get("hotkey")!.GetValue<string>() == "not-a-key",
            "field-save-preserves-invalid-and-unknown-raw-plus-other-unsaved-model-edit");
        var repeat = await raw.SaveFieldsAsync(rawRoot, [visibility]);
        check(!repeat.Succeeded && repeat.Error is null && JsonNode.DeepEquals(await Read(rawRoot), expectedRaw),
            "field-save-success-advances-only-persisted-baseline");
        check((await raw.SaveAsync(rawRoot)).Succeeded && (await Read(rawRoot))["sheetOpacity"]!.GetValue<double>() == .73,
            "field-save-retained-draft-is-available-to-later-full-save");

        var badActions = new JsonObject { ["malformed"] = true };
        var (mapKeyRoot, mapKey) = await Load("map-key-with-unselected-bad-actions",
            new JsonObject { ["hotkey"] = "G", ["actionHotkeys"] = badActions.DeepClone() });
        mapKey.Set("hotkey", "H");
        var mapKeySaved = await mapKey.SaveFieldsAsync(mapKeyRoot, ["hotkey"]);
        check(mapKeySaved.Succeeded && (await Read(mapKeyRoot))["hotkey"]!.GetValue<string>() == "H"
            && JsonNode.DeepEquals((await Read(mapKeyRoot))["actionHotkeys"], badActions),
            "field-save-map-key-uses-typed-actions-while-preserving-unselected-malformed-raw");

        var (repairRoot, repair) = await Load("explicit-repair", new JsonObject { [visibility] = "invalid boolean", ["futureField"] = 17 });
        repair.Set(visibility, true);
        check((await repair.SaveFieldsAsync(repairRoot, [visibility])).Succeeded
              && (await Read(repairRoot))[visibility]!.GetValue<bool>(), "field-save-explicit-repair-saves-even-when-typed-default-is-unchanged");

        var (freshRoot, fresh) = await Load("fresh-file");
        var freshDefaults = fresh.ActionHotkeys.ToArray();
        var actionDraft = freshDefaults.ToArray();
        actionDraft[0] = "F2";
        fresh.Set("actionHotkeys", new JsonArray(actionDraft.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray()));
        fresh.Set("sheetOpacity", .78);
        fresh.Set(visibility, false);
        check((await fresh.SaveFieldsAsync(freshRoot, [visibility])).Succeeded, "field-save-first-file-created");
        var freshSaved = await Read(freshRoot);
        var freshReload = (await SettingsDocument.LoadAsync(freshRoot)).Document!;
        check(freshSaved.Count == 2 && freshSaved[visibility]!.GetValue<bool>() == false
              && freshReload.ActionHotkeys.SequenceEqual(freshDefaults)
              && fresh.ActionHotkeys.SequenceEqual(actionDraft) && fresh.SheetOpacity == .78,
            "field-save-new-file-persists-fresh-shortcuts-without-accepting-shortcut-or-opacity-draft");
        check((await fresh.SaveAsync(freshRoot)).Succeeded && (await Read(freshRoot))["actionHotkeys"]![0]!.GetValue<string>() == "F2",
            "field-save-new-file-keeps-unsaved-shortcut-draft-for-later-full-save");

        var (legacyRoot, legacy) = await Load("legacy-file", new JsonObject());
        legacy.Set(visibility, false);
        check((await legacy.SaveFieldsAsync(legacyRoot, [visibility])).Succeeded
              && !(await Read(legacyRoot)).ContainsKey("actionHotkeys") && legacy.ActionHotkeys[0] == "F5",
            "field-save-existing-legacy-file-does-not-add-fresh-shortcuts");

        var (busyRoot, busy) = await Load("busy-file", new JsonObject { [visibility] = true, ["sheetOpacity"] = .55 });
        busy.Set(visibility, false);
        busy.Set("sheetOpacity", .74);
        var busyGate = Gate(busy);
        await busyGate.WaitAsync();
        Task<DocumentSaveResult> pendingFull;
        try
        {
            pendingFull = busy.SaveAsync(busyRoot);
            check(!pendingFull.IsCompleted, "field-save-real-full-save-is-pending-at-existing-gate");
            var refused = busy.SaveFieldsAsync(busyRoot, [visibility]);
            check(refused.IsCompleted && !refused.Result.Succeeded && refused.Result.Error is null
                  && busy.SheetVisibilityEnabled == false && busy.SheetOpacity == .74
                  && (await Read(busyRoot))[visibility]!.GetValue<bool>(),
                "field-save-busy-is-immediate-does-not-queue-or-change-drafts");
        }
        finally { busyGate.Release(); }
        check((await pendingFull).Succeeded && (await Read(busyRoot))["sheetOpacity"]!.GetValue<double>() == .74,
            "field-save-busy-refusal-keeps-existing-full-save-behavior");

        // This is real isolated file I/O, not a mocked writer or a new
        // production test hook. The payload only makes the pending I/O window
        // observable; its duration is not a product performance expectation.
        var concurrentRaw = new JsonObject
        {
            [visibility] = true, ["sheetOpacity"] = .55,
            ["futurePayload"] = new string('x', 8 * 1024 * 1024)
        };
        var (concurrentRoot, concurrent) = await Load("capture-during-io", concurrentRaw);
        concurrent.Set(visibility, false);
        var first = concurrent.SaveFieldsAsync(concurrentRoot, [visibility]);
        check(!first.IsCompleted, "field-save-accepted-snapshot-has-real-pending-file-io");
        concurrent.Set(visibility, true);
        concurrent.Set("sheetOpacity", .91);
        var second = concurrent.SaveFieldsAsync(concurrentRoot, [visibility]);
        check(second.IsCompleted && !second.Result.Succeeded && second.Result.Error is null,
            "field-save-second-visibility-reversal-is-busy-without-queue");
        check((await first).Succeeded, "field-save-first-captured-value-finishes");
        var firstSaved = await Read(concurrentRoot);
        check(!firstSaved[visibility]!.GetValue<bool>() && firstSaved["sheetOpacity"]!.GetValue<double>() == .55
              && concurrent.SheetVisibilityEnabled && concurrent.SheetOpacity == .91,
            "field-save-samples-at-acceptance-and-retains-new-same-field-and-other-field-drafts");
        check((await concurrent.SaveFieldsAsync(concurrentRoot, [visibility])).Succeeded,
            "field-save-later-explicit-call-saves-newer-visibility");
        var secondSaved = await Read(concurrentRoot);
        check(secondSaved[visibility]!.GetValue<bool>() && secondSaved["sheetOpacity"]!.GetValue<double>() == .55
              && secondSaved["futurePayload"]!.GetValue<string>() == concurrentRaw["futurePayload"]!.GetValue<string>(),
            "field-save-next-call-uses-actual-last-saved-baseline");

        var (conflictRoot, conflict) = await Load("external-conflict", new JsonObject { [visibility] = true });
        var beforeExternal = await File.ReadAllBytesAsync(conflictRoot.UiSettingsPath);
        conflict.Set(visibility, false);
        await File.WriteAllTextAsync(conflictRoot.UiSettingsPath, "{\"external\":true}");
        var external = await File.ReadAllBytesAsync(conflictRoot.UiSettingsPath);
        var rejected = await conflict.SaveFieldsAsync(conflictRoot, [visibility]);
        var afterRejected = await File.ReadAllBytesAsync(conflictRoot.UiSettingsPath);
        check(!rejected.Succeeded && rejected.Error is IOException && !conflict.SheetVisibilityEnabled
              && external.SequenceEqual(afterRejected),
            "field-save-external-edit-is-not-overwritten-and-draft-is-retained");
        await File.WriteAllBytesAsync(conflictRoot.UiSettingsPath, beforeExternal);
        check((await conflict.SaveFieldsAsync(conflictRoot, [visibility])).Succeeded,
            "field-save-failed-conflict-keeps-original-baseline-for-explicit-retry");
        conflict.Set(visibility, true);
        var differentRoot = new ExperimentalDataRoot(Path.Combine(output, "wrong-root"));
        var wrongRoot = await conflict.SaveFieldsAsync(differentRoot, [visibility]);
        check(!wrongRoot.Succeeded && wrongRoot.Error is IOException && !File.Exists(differentRoot.UiSettingsPath)
              && conflict.SheetVisibilityEnabled && !(await Read(conflictRoot))[visibility]!.GetValue<bool>(),
            "field-save-enforces-existing-document-root-binding");

        var (failureRoot, failure) = await Load("io-failure");
        failure.Set(visibility, false);
        await File.WriteAllTextAsync(failureRoot.Path, "isolated test obstruction");
        var failed = await failure.SaveFieldsAsync(failureRoot, [visibility]);
        check(!failed.Succeeded && failed.Error is IOException && !failure.SheetVisibilityEnabled
              && await File.ReadAllTextAsync(failureRoot.Path) == "isolated test obstruction",
            "field-save-io-failure-retains-model-and-original-file-state");
        File.Delete(failureRoot.Path); // Only this test's exact obstruction, to allow an explicit retry.
        check((await failure.SaveFieldsAsync(failureRoot, [visibility])).Succeeded,
            "field-save-io-failure-does-not-advance-baseline-or-strand-save-gate");

        var (cancelRoot, cancel) = await Load("cancellation");
        cancel.Set(visibility, false);
        var cancelGate = Gate(cancel);
        await cancelGate.WaitAsync();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        bool canceled = false;
        try { await cancel.SaveFieldsAsync(cancelRoot, [visibility], cancellation.Token); }
        catch (OperationCanceledException) { canceled = true; }
        finally { cancelGate.Release(); }
        check(canceled && !File.Exists(cancelRoot.UiSettingsPath) && !cancel.SheetVisibilityEnabled,
            "field-save-pre-cancellation-propagates-and-preserves-draft");
        check((await cancel.SaveFieldsAsync(cancelRoot, [visibility])).Succeeded,
            "field-save-pre-cancellation-does-not-release-unowned-gate-or-strand-retry");

        var (contextRoot, context) = await Load("selected-context", new JsonObject { ["gameMode"] = "nightmare", ["gameSeason"] = "s0" });
        context.SetMany(new Dictionary<string, JsonNode?> { ["gameMode"] = JsonValue.Create("difficulty2"), ["gameSeason"] = JsonValue.Create("s1") });
        var splitContext = await context.SaveFieldsAsync(contextRoot, ["gameMode"]);
        check(!splitContext.Succeeded && splitContext.Error is ArgumentException
              && (await Read(contextRoot))["gameMode"]!.GetValue<string>() == "nightmare"
              && context.GameMode == "difficulty2" && context.GameSeason == "s1",
            "field-save-validates-selected-mode-against-persisted-season-without-saving-other-draft");
        check((await context.SaveFieldsAsync(contextRoot, ["gameMode", "gameSeason"])).Succeeded,
            "field-save-related-context-can-be-explicitly-saved-together");
    }
}
