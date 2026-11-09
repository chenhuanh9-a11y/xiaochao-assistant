using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;

internal static class VisibilityWorkspaceRegression
{
    internal static async Task Run(string output, Action<bool, string> check)
    {
        string data = Path.Combine(output, "visibility-ui"); Directory.CreateDirectory(data);
        string path = Path.Combine(data, SettingsDocument.FileName);
        byte[] original = JsonSerializer.SerializeToUtf8Bytes(new JsonObject {
            ["sheetVisibilityEnabled"] = false, ["hotkey"] = "G", ["sheetOpacity"] = .55,
            ["automaticFailureEvidence"] = null, ["future"] = new JsonArray(1, 2, 3)
        });
        await File.WriteAllBytesAsync(path, original);
        var shell = new WorkspaceWindow(data, null);
        var page = Field<RuntimePage>(shell, "runtimePage");
        var button = Field<Button>(page, "toggleVisibility");
        try
        {
            check(!button.IsEnabled, "visibility-button-waits-for-initial-settings-load");
            await Call(shell, "ToggleAlignedVisibility");
            check(page.AlignedVisible && original.SequenceEqual(File.ReadAllBytes(path)),
                "visibility-during-startup-is-refused-without-changing-or-saving-an-unloaded-preference");
            await Call(shell, "LoadSettings");
            check(!page.AlignedVisible && button.IsEnabled && (string)button.Content == "显示路线",
                "visibility-load-restores-disk-preference-and-the-actual-button-label");
            Field<TextBox>(shell, "hotkey").Text = "H";
            Field<Slider>(shell, "sheetOpacity").Value = .7;
            button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            if (Field<Task?>(shell, "visibilitySave") is { } save) await save;
            var saved = JsonNode.Parse(await File.ReadAllTextAsync(path))!;
            check(page.AlignedVisible && (string)button.Content == "隐藏路线" && saved["sheetVisibilityEnabled"]!.GetValue<bool>(),
                "visibility-real-button-toggles-immediately-and-persists-its-own-field");
            check(saved["hotkey"]!.GetValue<string>() == "G" && saved["sheetOpacity"]!.GetValue<double>() == .55
                && saved["automaticFailureEvidence"] is null && saved["future"]!.AsArray().Count == 3
                && Field<HashSet<string>>(shell, "editedSettings").SetEquals(["hotkey", "sheetOpacity"]),
                "visibility-field-save-does-not-save-other-ui-drafts-or-repair-unselected-raw-data");

            var bindings = GlobalInputBindings.FromSettings(Field<SettingsDocument>(shell, "settings"));
            var source = new FixtureInputSource(bindings);
            int keyDeliveries = 0;
            bool passedThrough = true;
            await using (var host = new WpfGlobalInputController(shell.Dispatcher, bindings,
                () => new(new(42, true, 42, false, true, true)), delivery =>
                {
                    if (delivery.Notification.Key is { } key) { keyDeliveries++; passedThrough &= !key.Consumed; }
                    typeof(WorkspaceWindow).GetMethod("ConsumeInput", BindingFlags.Instance | BindingFlags.NonPublic)!
                        .Invoke(shell, [delivery]);
                }, source))
            {
                check((await host.StartAsync()).Started, "visibility-input-fixture-starts-the-real-policy-and-wpf-delivery-without-os-hooks");
                var chord = bindings.Items.Single(binding => binding.Action == "toggle-sheet-visibility");
                check(chord.VirtualKey == 0x7a && chord.Modifiers == GlobalInputModifiers.None,
                    "visibility-default-action-is-the-configured-F11-binding");
                source.Key(chord.VirtualKey, true);
                await WaitFor(() => keyDeliveries == 1);
                if (Field<Task?>(shell, "visibilitySave") is { } firstKeySave) await firstKeySave;
                check(!page.AlignedVisible && !JsonNode.Parse(File.ReadAllText(path))!["sheetVisibilityEnabled"]!.GetValue<bool>(),
                    "visibility-F11-delivery-enters-the-real-workspace-consumer-and-persists-only-visibility");
                source.Key(chord.VirtualKey, false); source.Key(chord.VirtualKey, true);
                await WaitFor(() => keyDeliveries == 3);
                if (Field<Task?>(shell, "visibilitySave") is { } nextKeySave) await nextKeySave;
                check(page.AlignedVisible && passedThrough && Field<HashSet<string>>(shell, "editedSettings").SetEquals(["hotkey", "sheetOpacity"]),
                    "visibility-next-F11-edge-restores-the-preference-and-never-consumes-the-original-key-or-other-drafts");
            }

            var document = Field<SettingsDocument>(shell, "settings");
            var gate = Field<SemaphoreSlim>(document, "_saveGate"); gate.Wait();
            byte[] beforeBusy = await File.ReadAllBytesAsync(path);
            try
            {
                button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                if (Field<Task?>(shell, "visibilitySave") is { } busy) await busy;
                check(!page.AlignedVisible && beforeBusy.SequenceEqual(File.ReadAllBytes(path))
                    && Field<HashSet<string>>(shell, "editedSettings").Contains("sheetVisibilityEnabled"),
                    "visibility-while-save-busy-keeps-immediate-state-and-dirty-draft-without-queuing");
            }
            finally { gate.Release(); }
            await Call(shell, "SaveSettings");
            saved = JsonNode.Parse(await File.ReadAllTextAsync(path))!;
            check(!saved["sheetVisibilityEnabled"]!.GetValue<bool>() && saved["hotkey"]!.GetValue<string>() == "H"
                && saved["sheetOpacity"]!.GetValue<double>() == .7,
                "visibility-unsaved-busy-choice-can-be-saved-later-by-the-explicit-settings-action");

            await File.WriteAllTextAsync(path, "{\"outside\":true}");
            button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            if (Field<Task?>(shell, "visibilitySave") is { } failed) await failed;
            check(page.AlignedVisible && File.ReadAllText(path) == "{\"outside\":true}"
                && Field<TextBlock>(shell, "settingsState").Text.Contains("未保存"),
                "visibility-save-conflict-keeps-immediate-display-choice-and-the-external-file");
            check(!shell.IsVisible && page.State is null
                && !Field<WpfGlobalInputController>(shell, "inputController").ListeningRequested
                && Field<WindowsRuntimeCapturePort>(shell, "runtimeCapture").Binding is null,
                "visibility-ui-regression-does-not-show-bind-capture-or-start-native-input");
        }
        finally
        {
            await DrainClose(shell);
        }

        string closingData = Path.Combine(output, "visibility-close-write"); Directory.CreateDirectory(closingData);
        string closingPath = Path.Combine(closingData, SettingsDocument.FileName);
        await File.WriteAllTextAsync(closingPath, new JsonObject { ["sheetVisibilityEnabled"] = true,
            ["futurePayload"] = new string('x', 8 * 1024 * 1024) }.ToJsonString());
        var closingShell = new WorkspaceWindow(closingData, null);
        try
        {
            await Call(closingShell, "LoadSettings");
            Task saving = Call(closingShell, "ToggleAlignedVisibility");
            check(!saving.IsCompleted, "visibility-close-case-has-an-accepted-real-file-write-in-flight");
            await DrainClose(closingShell); await saving;
            var closed = JsonNode.Parse(await File.ReadAllTextAsync(closingPath))!;
            check(!closed["sheetVisibilityEnabled"]!.GetValue<bool>() && closed["futurePayload"]!.GetValue<string>().Length == 8 * 1024 * 1024,
                "visibility-normal-close-drains-the-accepted-preference-write-and-preserves-other-raw-data");
        }
        finally { if (!Field<bool>(closingShell, "closeReady")) await DrainClose(closingShell); }
    }

    static async Task WaitFor(Func<bool> ready)
    {
        var started = System.Diagnostics.Stopwatch.StartNew();
        while (!ready())
        {
            if (started.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("Visibility input delivery did not drain.");
            await Dispatcher.Yield(DispatcherPriority.Background);
        }
    }

    static async Task DrainClose(WorkspaceWindow shell)
    {
        shell.Close();
        var started = System.Diagnostics.Stopwatch.StartNew();
        while (!Field<bool>(shell, "closeReady"))
        {
            if (started.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("Visibility test window did not drain.");
            await Dispatcher.Yield(DispatcherPriority.Background);
        }
        await Dispatcher.Yield(DispatcherPriority.Background);
    }

    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;
    static Task Call(object owner, string name) => (Task)owner.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(owner, null)!;
}
