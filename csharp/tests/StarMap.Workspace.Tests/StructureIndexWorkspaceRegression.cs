using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Settings;

internal static class StructureIndexWorkspaceRegression
{
    internal static async Task Run(string resources, string replay, string output, Action<bool, string> check)
    {
        var samples = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(samples); var page = new RuntimePage(capture, _ => { }); var evidence = new JsonArray();
        try
        {
            check(await page.Configure(resources, new SettingsDocument(new JsonObject {
                ["gameSeason"] = "s1", ["gameMode"] = "difficult", ["routeProfileId"] = "shared-structure" })),
                "index-workspace-uses-real-configuration-and-session");
            await Action("start_round"); string? lastFrame = null;
            foreach (int number in new[] { 255, 257, 261, 265, 303, 305, 284, 307 })
            {
                string id = "batch02-event-" + number.ToString("D6"); capture.Select(id, false); await Action("capture_now");
                var state = page.State!; var packet = page.ReadRender();
                check(packet is not null && state.NativeGeometryVerified && state.Display.CanonicalVerified
                    && packet.Stamp.FrameId == state.Context.FrameId && state.Context.FrameId != lastFrame,
                    id + "-real-session-provides-fresh-current-material-to-WPF");
                var ready = await Task.Run(() => WpfDisplayMaterialConsumer.PrepareMaterialReady(packet!.Material));
                check(ready.IsFrozen && ready.PixelWidth == packet!.Material.Width && !page.SessionView!.Runtime.LiveAuthorization,
                    id + "-actual-WPF-bitmap-consumer-freezes-current-recorded-pixels");
                var source = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, id, "recognition.json")))!["poses"]![packet!.MapId]!;
                var wanted = source["matrixSourceToClient"]!.Deserialize<double[][]>()!;
                check(state.NativePose!.ToMatrix().SelectMany(row => row).Zip(wanted.SelectMany(row => row))
                    .All(pair => Math.Abs(pair.First-pair.Second) <= 1e-5), id + "-original-native-matrix-reaches-the-real-WPF-consumer");
                int expected = number == 255 ? 14 : number == 307 ? 1 : 3;
                check(state.RemainingMapIds.Count == expected && state.RecognitionPath == "structure-index"
                    && (number == 307 ? state.IdentityLocked && state.Context.MapId == "106008" && state.ProvisionalMapId is null
                        : !state.IdentityLocked && state.Context.MapId is null && state.ProvisionalMapId == packet!.MapId),
                    id + "-same-page-session-distinguishes-temporary-reference-from-final-index-lock");
                evidence.Add(JsonSerializer.SerializeToNode(new { id, state, materialMap = packet!.MapId, ready.PixelWidth, ready.PixelHeight, ready.IsFrozen }));
                lastFrame = state.Context.FrameId;
            }
            check(!page.IsVisible && page.SessionView!.StaleResults == 0, "index-workspace-hidden-lifecycle-needs-no-native-window-or-input");
            await Action("close_map");
            check(page.ReadRender() is null && page.State!.RemainingMapIds.Count == 1 && page.State.IdentityLocked,
                "index-workspace-close-revokes-pixels-and-keeps-round-identity");
            await File.WriteAllTextAsync(Path.Combine(output, "structure-index-workspace.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
        finally { await page.CloseAsync(); }
        async Task Action(string action) { page.Dispatch(action); await page.DrainAsync(); }
    }
}
