using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Settings;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class S1ExplicitDomainWorkspaceRegression
{
    internal static async Task Run(string resources, string replay, string originalHostOracle, string output, Action<bool, string> check)
    {
        var samples = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var originals = JsonNode.Parse(await File.ReadAllBytesAsync(originalHostOracle))!["cases"]!.AsArray()
            .Where(row => row!["explicitDomain"]!.GetValue<bool>()).ToDictionary(row => row!["raw"]!["id"]!.GetValue<string>(), row => row!);
        var capture = new RecordedCapture(samples);
        var page = new RuntimePage(capture, _ => { });
        using var native = new WindowsRuntimeCapturePort();
        var reports = new List<string>(); var evidence = new List<object>();
        var consumed = new List<SessionCompletion>();
        RuntimeOverlayHost? host = null;
        try
        {
            check(await page.Configure(resources, new SettingsDocument(new JsonObject {
                ["gameSeason"] = "s1", ["gameMode"] = "nightmare", ["routeProfileId"] = "shared-structure", ["idvb0059Enabled"] = true })),
                "S1-explicit-original-Host-oracle-binds-enabled-setting-and-real-resources");
            Field<SessionService>(page, "session").ResultConsumed += consumed.Add;
            await Action("start_round"); await Capture("batch02-event-000315");
            await Action("confirm_route", new { mapId = "107012" }); Compare("batch02-event-000315");
            var runtime = Field<LiveRuntime>(page, "runtime");
            var material = page.ReadRender()!;
            var ready = await Task.Run(() => WpfDisplayMaterialConsumer.PrepareMaterialReady(material.Material));
            check(ready.IsFrozen && !page.RememberMaterialReady(material), "S1-full-frame-material-is-ready-without-requesting-0059-learning");
            await runtime.RememberRenderMaterialAsync(material.MaterialToken, runtime.ReadState().Context, default);
            page.SetAlignedVisibility(false);
            host = new(Dispatcher.CurrentDispatcher, page.ReadRender, native, reports.Add,
                visible: () => page.AlignedVisible, readPendingMaterial: page.ReadPendingGroupMaterial,
                acceptMaterialReady: page.AcceptGroupMaterialReady, rejectMaterial: page.RejectGroupMaterial,
                readMaterialToRemember: page.ReadMaterialToRemember, rememberMaterialReady: page.RememberMaterialReady);
            host.RefreshVisibility();
            await Capture("batch02-event-000317");
            check(!Attempted(), "S1-original-explicit-domain-skips-steady-even-after-material-ACK");
            Compare("batch02-event-000317");
            foreach (int number in new[] { 318, 319, 323, 323 })
            { string id = "batch02-event-" + number.ToString("D6"); await Capture(id); Compare(id); }
            await Action("end_round");
            check(await page.Configure(resources, new SettingsDocument(new JsonObject {
                ["gameSeason"] = "s1", ["gameMode"] = "difficult", ["routeProfileId"] = "shared-structure", ["idvb0059Enabled"] = true })),
                "S1-mode-change-uses-the-production-settings-and-Session-reconfiguration-entry");
            page.SetAlignedVisibility(false);
            await Action("start_round"); await Capture("batch02-event-000255");
            check(!page.State!.IdentityLocked && page.State.Context.MapId is null && page.State.RemainingMapIds.Count == 14
                && page.ReadRender()?.MapId == page.State.ProvisionalMapId && page.State.ProvisionalMapId == "106008",
                "S1-difficult-first-compatible-material-does-not-exclude-unknown-maps-or-invent-identity");
            // Explicit numerical reference, not a unique identity or user answer.
            await Action("confirm_route", new { mapId = "106008" }); Compare("batch02-event-000255");
            foreach (int number in Enumerable.Range(256, 6))
            { string id = "batch02-event-" + number.ToString("D6"); await Capture(id); Compare(id); }
            // Separate fresh-round negative case, not appended to the natural sequence.
            await Action("end_round"); await Action("start_round"); await Capture("batch02-event-000254");
            await Action("confirm_route", new { mapId = "106008" }); Compare("batch02-event-000254");
            await File.WriteAllTextAsync(Path.Combine(output, "s1-explicit-domain.json"), JsonSerializer.Serialize(new {
                evidence, session = page.SessionView, reports, runtimeSnapshot = runtime.ReadSnapshot(),
                hasNativeBinding = native.Binding is not null, nativeHandle = Field<WpfAlignedOverlaySurface>(host, "surface").NativeHandle.ToInt64(), consumed,
                scope = "Original _AlignmentHost.fit explicit-full-frame bounds compared after actual C# RuntimePage/Session/fit/canonical/material. WPF raw material genuinely frozen; no Show/game/input. C1 is user-confirmed;106008 is only an unconfirmed compatible reference. Source original calls zero steady; direct component experiments do not establish Runtime eligibility."
            }, new JsonSerializerOptions { WriteIndented = true }));
            check(!runtime.ReadSnapshot().LiveAuthorization, "S1-original-full-frame-flow-never-grants-live-authorization");
            check(reports.Count == 0 && native.Binding is null && Field<WpfAlignedOverlaySurface>(host, "surface").NativeHandle == 0,
                "S1-original-full-frame-flow-never-creates-a-native-overlay-or-error");
            check(page.SessionView!.StaleResults == 0, "S1-original-full-frame-flow-does-not-produce-stale-session-results");
        }
        finally { host?.Dispose(); await page.CloseAsync(); }

        bool Attempted() => page.State!.Diagnostics.GetProperty("steadyAttempt").GetProperty("attempted").GetBoolean();
        async Task Action(string action, object? payload = null) { page.Dispatch(action, payload); await page.DrainAsync(); }
        async Task Capture(string id) { capture.Select(id); await Action("capture_now"); }
        void Compare(string id)
        {
            var state = page.State!;
            var oracle = originals[id]; var expected = oracle["fit"]!;
            bool success = expected["success"]!.GetValue<bool>();
            check(oracle["steadyCalls"]!.GetValue<int>() == 0 && !Attempted()
                && state.Diagnostics.GetProperty("steadyAttempt").GetProperty("reason").GetString() == "explicit-domain-independent-fit",
                id + "-uses-original-explicit-domain-with-zero-steady-calls");
            check(state.NativeGeometryVerified == success && state.Reason == expected["reason"]!.GetValue<string>(),
                id + "-full-frame-fit-verdict-and-reason-match-actual-original-Host");
            if (success)
            {
                var wanted = expected["matrixSourceToClient"]!.Deserialize<double[][]>()!;
                check(state.NativePose!.ToMatrix().SelectMany(row => row).Zip(wanted.SelectMany(row => row))
                    .All(pair => Math.Abs(pair.First - pair.Second) <= 1e-5) && state.Display.CanonicalVerified && page.ReadRender() is not null,
                    id + "-original-full-frame-matrix-reaches-canonical-and-material");
            }
            else check(state.NativePose is null && page.ReadRender() is null && state.IdentityLocked,
                id + "-original-full-frame-rejection-withholds-old-pose-and-material-but-retains-manual-reference");
            check(page.ReadMaterialToRemember() is null, id + "-does-not-learn-a-steady-scale-from-S1-material");
            evidence.Add(new { id, state });
        }
    }
    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(owner)!;
}
