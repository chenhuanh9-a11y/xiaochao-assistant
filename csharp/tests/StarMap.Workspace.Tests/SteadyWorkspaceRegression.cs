using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class SteadyWorkspaceRegression
{
    internal static async Task Run(string resources, string originalReplay, string steadyOracle,
        string output, Action<bool, string> check)
    {
        var samples = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var expectations = JsonNode.Parse(await File.ReadAllBytesAsync(steadyOracle))!["cases"]!.AsArray();
        var capture = new RecordedCapture(samples);
        var page = new RuntimePage(capture, _ => { });
        using var nativeCapture = new WindowsRuntimeCapturePort();
        RuntimeOverlayHost? host = null;
        var messages = new List<string>();
        var evidence = new List<object>();
        try
        {
            check(await page.Configure(resources, SettingsDocument.CreateNew()), "steady-workspace-binds-current-resources");
            await Action("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
            await Action("start_round");
            await Capture("batch02-event-000315");
            await Action("confirm_route", new { mapId = "107012" });
            var runtime = Field<LiveRuntime>(page, "runtime");
            check(page.State!.NativeGeometryVerified && !Attempted(), "steady-first-C1-fit-is-cold-with-no-trusted-floor-scale");
            var unremembered = page.ReadRender();
            await Capture("batch02-event-000315");
            check(!Attempted() && runtime.ReadMaterialToRemember() == page.ReadRender()
                && !ReferenceEquals(unremembered, page.ReadRender()), "steady-successful-fit-without-material-ACK-does-not-learn-scale");
            page.SetAlignedVisibility(false);
            StartHost();
            await Remembered();
            var sequence = expectations.Single(row => row!["id"]!.GetValue<string>() == "s1-c1-current-sequence")!;
            foreach (var step in sequence["steps"]!.AsArray())
            {
                string id = step!["id"]!.GetValue<string>();
                await Capture(id);
                // These expectations are read only after production Solve has
                // consumed this raw frame and its own material-committed prior.
                var actual = page.State!;
                var attempt = actual.Diagnostics.GetProperty("steadyAttempt");
                bool accepted = step["expectedWallclock"]!["accepted"]!.GetValue<bool>();
                check(Attempted() && attempt.GetProperty("accepted").GetBoolean() == accepted,
                    id + "-production-steady-admission-matches-original-current-frame-sequence");
                if (accepted)
                {
                    var matrix = actual.Diagnostics.GetProperty("fit").GetProperty("matrixSourceToClient").Deserialize<double[][]>()!;
                    check(actual.Reason == "idvb0059-steady" && actual.NativeGeometryVerified && actual.NativePose!.ToMatrix()
                        .SelectMany(row => row).SequenceEqual(matrix.SelectMany(row => row)),
                        id + "-steady-proposal-is-the-pose-consumed-by-canonical-display");
                }
                else check(actual.NativeGeometryVerified && actual.Reason != "idvb0059-steady"
                    && attempt.GetProperty("reason").GetString() == step["expectedWallclock"]!["reason"]!.GetValue<string>(),
                    id + "-steady-rejection-runs-real-cold-fallback-without-relaxing-admission");
                evidence.Add(new { id, state = actual });
                await Remembered();
            }
            await Capture("batch02-event-000323");
            check(page.State!.Context.FloorId == "-2" && page.State.NativeGeometryVerified && !Attempted(),
                "steady-other-floor-first-fit-never-borrows-the-previous-floor-scale");
            await Remembered();
            await Capture("batch02-event-000323");
            check(Attempted() && page.State!.Reason == "idvb0059-steady", "steady-other-floor-repeat-uses-only-its-own-material-committed-scale");
            await Remembered();
            StopHost();
            await Action("end_round"); await Action("start_round");
            await Capture("batch02-event-000315"); await Action("confirm_route", new { mapId = "107012" });
            check(!Attempted(), "steady-new-round-discards-all-previous-floor-priors");
            var late = page.ReadRender()!; var lifetime = runtime.ReadState().Context;
            await Action("close_map");
            check(!page.RememberMaterialReady(late), "steady-closed-map-refuses-a-late-WPF-material-callback");
            await runtime.RememberRenderMaterialAsync(late.MaterialToken, lifetime, default);
            await Capture("batch02-event-000315");
            check(!Attempted(), "steady-private-maintenance-rechecks-closed-material-lifetime-before-learning");
            StartHost(); await Remembered(); StopHost();
            runtime.InvalidateCapture("window-binding-changed");
            await Capture("batch02-event-000315");
            check(!Attempted(), "steady-window-rebinding-discards-the-display-environment-prior");

            foreach (var item in new[] {
                (Mode: "nightmare", Id: "batch01-event-000006", Map: "nightmare-12"),
                (Mode: "difficult", Id: "batch02-event-000147", Map: "difficult-map-15") })
            {
                await Action("end_round");
                await Action("set_context", new { season = "s0", mode = item.Mode, profileId = "shared-structure" });
                await Action("start_round"); await Capture(item.Id);
                await Action("confirm_route", new { mapId = item.Map });
                check(page.State!.NativeGeometryVerified && !Attempted(), item.Id + "-S0-reference-starts-through-real-cold-Host");
                StartHost(); await Remembered(); await Capture(item.Id);
                check(Attempted() && page.State!.Reason == "idvb0059-steady"
                    && page.State.Diagnostics.GetProperty("stages").EnumerateArray().Any(row => row.GetProperty("stage").GetString() == "steady"),
                    item.Id + "-S0-shared-Host-consumes-the-same-production-steady-producer");
                evidence.Add(new { id = item.Id, state = page.State });
                await Remembered(); StopHost();
            }
            await Action("end_round");
            await Action("set_context", new { season = "s1", mode = "difficult", profileId = "shared-structure" });
            await Action("start_round"); await Capture("batch02-event-000255");
            check(!page.State!.IdentityLocked && page.State.Candidates.Count == 5,
                "steady-S1-difficult-compatible-references-remain-unconfirmed");
            // Explicit reference fixture, not the user's answer or a claim of
            // unique identity. The user could not distinguish these maps.
            await Action("confirm_route", new { mapId = "106008" });
            check(page.State!.NativeGeometryVerified && !Attempted(), "steady-S1-difficult-reference-starts-cold");
            StartHost(); await Remembered();
            var difficult = expectations.Single(row => row!["id"]!.GetValue<string>() == "s1-difficult-unconfirmed-reference-series")!;
            foreach (var step in difficult["steps"]!.AsArray())
            {
                string id = step!["id"]!.GetValue<string>();
                await Capture(id);
                check(Attempted() && page.State!.Diagnostics.GetProperty("steadyAttempt").GetProperty("accepted").GetBoolean()
                    == step["expectedWallclock"]!["accepted"]!.GetValue<bool>() && page.State.Reason == "idvb0059-steady",
                    id + "-S1-difficult-reference-current-frame-steady-matches-original-admission");
                evidence.Add(new { id, state = page.State });
                await Remembered();
            }
            StopHost();
            check(messages.Count == 0 && !page.SessionView!.Runtime.LiveAuthorization && page.ReadRender() is { IsLive: false },
                "steady-material-learning-preserves-recorded-evidence-and-issues-no-native-display-grant");
            var disabled = new SettingsDocument(new JsonObject { ["gameSeason"] = "s1", ["gameMode"] = "difficult",
                ["routeProfileId"] = "shared-structure", ["idvb0059Enabled"] = false });
            check(!await page.Configure(resources, disabled), "steady-solver-setting-cannot-replace-an-active-round");
            await Action("end_round");
            check(await page.Configure(resources, disabled), "steady-disabled-import-is-applied-on-the-idle-runtime");
            await Action("start_round"); await Capture("batch02-event-000255");
            await Action("confirm_route", new { mapId = "106008" });
            check(page.State!.NativeGeometryVerified && page.ReadMaterialToRemember() is null,
                "steady-disabled-runtime-keeps-real-cold-fit-but-does-not-propose-learning");
            await Capture("batch02-event-000256");
            check(!Attempted() && page.State!.Reason != "idvb0059-steady" && page.State.NativeGeometryVerified,
                "steady-disabled-runtime-does-not-enter-the-steady-producer");
            await File.WriteAllTextAsync(Path.Combine(output, "steady-workspace.json"), JsonSerializer.Serialize(new {
                evidence, session = page.SessionView, messages,
                scope = "Production RuntimePage, Session, 0059, resource cache and WPF frozen BitmapSource consumer; raw original 2026-11-09 frames. Only capture port replaced. Hidden, no Show/input/live game or GPU-present claim. S0 and S1-difficult choices are reference fixtures, not unique map truth."
            }, new JsonSerializerOptions { WriteIndented = true }));
        }
        finally { StopHost(); await page.CloseAsync(); }

        bool Attempted() => page.State!.Diagnostics.TryGetProperty("steadyAttempt", out var value)
            && value.GetProperty("attempted").GetBoolean();
        async Task Action(string name, object? payload = null) { page.Dispatch(name, payload); await page.DrainAsync(); }
        async Task Capture(string id) { capture.Select(id); await Action("capture_now"); }
        void StartHost()
        {
            host = new(Dispatcher.CurrentDispatcher, page.ReadRender, nativeCapture, messages.Add,
                visible: () => page.AlignedVisible, readPendingMaterial: page.ReadPendingGroupMaterial,
                acceptMaterialReady: page.AcceptGroupMaterialReady, rejectMaterial: page.RejectGroupMaterial,
                readMaterialToRemember: page.ReadMaterialToRemember, rememberMaterialReady: page.RememberMaterialReady);
            host.RefreshVisibility();
        }
        void StopHost() { host?.Dispose(); host = null; }
        async Task Remembered()
        {
            var runtime = Field<LiveRuntime>(page, "runtime");
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (runtime.ReadMaterialToRemember() is not null || page.SessionView!.WorkerActive)
            {
                if (watch.Elapsed > TimeSpan.FromSeconds(10)) throw new TimeoutException("Steady WPF material acknowledgement did not settle.");
                await Task.Delay(10);
            }
            await page.DrainAsync();
            var surface = Field<WpfAlignedOverlaySurface>(host!, "surface");
            check(surface.NativeHandle == 0 && !surface.IsVisible && nativeCapture.Binding is null,
                "steady-hidden-recorded-material-ACK-does-not-create-a-native-overlay");
        }
    }

    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(owner)!;
}
