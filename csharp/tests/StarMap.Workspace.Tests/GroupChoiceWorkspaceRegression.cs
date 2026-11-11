using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class GroupChoiceWorkspaceRegression
{
    internal static async Task Run(string resources, string originalReplay, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(index); capture.Select("batch01-event-000006");
        // Ordinary no-door compatibility uses exactly the derived pixels whose
        // group behavior was independently replayed by the original Runtime.
        capture.TransformPixels=S0GlyphAblation.Create(originalReplay,capture);
        var page = new RuntimePage(capture, _ => { });
        RuntimeOverlayHost? host = null;
        using var nativeCapture = new WindowsRuntimeCapturePort();
        try
        {
            check(await page.Configure(resources, SettingsDocument.CreateNew()), "group-workspace-binds-real-resources-through-session");
            page.Dispatch("start_round"); await page.DrainAsync();
            page.Dispatch("capture_now"); await page.DrainAsync();
            var runtime = Field<LiveRuntime>(page, "runtime");
            var session = Field<SessionService>(page, "session");
            var consumed = new List<SessionCompletion>(); session.ResultConsumed += consumed.Add;
            var before = page.State!; var predecessor = page.ReadRender();
            check(before.IdentityKind == "group" && before.Context.MapId != "nightmare-12" && predecessor is not null,
                "group-workspace-case-starts-from-a-different-real-provisional-member");
            page.Dispatch("select_group_member", new { mapId = "nightmare-31", groupId = before.IdentityGroupId,
                groupGeneration = before.GroupChoice.Generation, cycleId = before.GroupChoice.CycleId, floor = before.Context.FloorId });
            await page.DrainAsync();
            page.RefreshGroupControls();
            check(page.SessionView?.LastReceipt is { Success: false, Message: "只能选择当前相似组内的地图" }
                && Field<TextBlock>(page, "groupChoiceMessage").Text.Contains("只能选择当前相似组内的地图")
                && ReferenceEquals(page.ReadRender(), predecessor), "group-outside-member-rejection-reaches-the-real-session-as-readable-Chinese-without-changing-pixels");
            page.RefreshGroupControls();
            var members = Field<ComboBox>(page, "groupMember"); var choose = Field<Button>(page, "chooseGroupMember");
            check(choose.IsEnabled && members.Items.Count == 3 && members.Items.Cast<RuntimeGroupMember>()
                .Select(row => row.MapId).SequenceEqual(before.IdentityMemberIds), "group-real-control-lists-only-registered-members-and-is-enabled-after-session-collection");
            members.SelectedValue = "nightmare-12";
            choose.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            // Hold only UI receipt delivery by not pumping this owner. The actual
            // worker, original frame, numerical fit and candidate producer run.
            bool stagedBeforeCollection = SpinWait.SpinUntil(() => runtime.ReadPendingGroupMaterial() is not null,
                TimeSpan.FromSeconds(10));
            var pending = runtime.ReadPendingGroupMaterial();
            check(stagedBeforeCollection && session.Read().WorkerActive && pending is not null
                && page.ReadPendingGroupMaterial() is null && !page.AcceptGroupMaterialReady(pending),
                "group-material-ACK-cannot-overtake-the-real-session-result-collection");
            check(page.State!.Context.MapId == before.Context.MapId && ReferenceEquals(page.ReadRender(), predecessor),
                "group-real-button-keeps-committed-identity-and-pixels-until-consumer-readiness");
            await page.DrainAsync();
            check(consumed.Any(item => item.Action == "select_group_member" && item.Disposition == "Accepted")
                && consumed.All(item => item.Disposition != "ContextMismatch") && ReferenceEquals(page.ReadPendingGroupMaterial(), pending),
                "group-selection-receipt-is-accepted-before-the-material-callback-can-change-map-context");
            members.SelectedValue = "nightmare-19"; choose.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await page.DrainAsync(); page.RefreshGroupControls();
            string pendingName = before.GroupChoice.Members.Single(member => member.MapId == "nightmare-12").Name;
            check(page.SessionView?.LastReceipt is { Success: false, Reason: "choice-pending" }
                && Field<TextBlock>(page, "groupChoiceMessage").Text.Contains("正在核对上次选择")
                && Field<TextBlock>(page, "groupChoiceMessage").Text.Contains(pendingName)
                && ReferenceEquals(page.ReadPendingGroupMaterial(), pending),
                "group-pending-rejection-is-visible-and-names-the-actual-pending-member-instead-of-the-new-draft");
            members.SelectedValue = "nightmare-12";
            var image = await Task.Run(() => WpfDisplayMaterialConsumer.PrepareMaterialReady(pending!.Material));
            byte[] roundTrip = new byte[checked(pending!.Material.Stride * pending.Material.Height)];
            image.CopyPixels(roundTrip, pending.Material.Stride, 0);
            check(image.IsFrozen && image.PixelWidth == pending.Material.Width && image.PixelHeight == pending.Material.Height
                && roundTrip.SequenceEqual(pending.Material.CopyPixels()), "group-real-WPF-material-consumer-freezes-and-preserves-every-produced-BGRA-pixel");
            page.SetAlignedVisibility(false);
            var messages = new List<string>();
            host = new(System.Windows.Threading.Dispatcher.CurrentDispatcher, page.ReadRender, nativeCapture, messages.Add,
                visible: () => page.AlignedVisible, readPendingMaterial: page.ReadPendingGroupMaterial,
                acceptMaterialReady: page.AcceptGroupMaterialReady, rejectMaterial: page.RejectGroupMaterial);
            await Until(() => page.State?.GroupChoice.Status == "confirmed");
            check(page.State!.Context.MapId == "nightmare-12" && page.State.GroupChoice.HeldMapId == "nightmare-12"
                && ReferenceEquals(page.ReadRender(), pending) && !page.AlignedVisible && capture.Calls == 1,
                "group-production-host-consumes-hidden-material-and-commits-the-recorded-choice-without-recapture");
            var surface = Field<WpfAlignedOverlaySurface>(host, "surface");
            check(!surface.IsVisible && surface.NativeHandle == 0 && nativeCapture.Binding is null && messages.Count == 0
                && page.SessionView is { WorkerActive: false, Busy: false, StaleResults: 0 }
                && page.ReadRender() is { IsLive: false } && !page.SessionView.Runtime.LiveAuthorization,
                "group-recorded-readiness-never-creates-HWND-binds-a-window-or-grants-live-display-and-has-no-stale-session-result");
            page.RefreshGroupControls();
            check(members.SelectedValue as string == "nightmare-12" && choose.IsEnabled,
                "group-controls-retain-the-user-selection-after-material-commit");
            host.Dispose(); host = null;
            page.Dispatch("end_round"); await page.DrainAsync(); page.Dispatch("start_round"); await page.DrainAsync();
            capture.Select("batch01-event-000006"); page.Dispatch("capture_now"); await page.DrainAsync();
            page.Dispatch("close_map"); await page.DrainAsync(); page.RefreshGroupControls();
            members.SelectedValue = "nightmare-12"; choose.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await page.DrainAsync();
            capture.Select("batch02-event-000018"); page.Dispatch("capture_now"); await page.DrainAsync(); page.RefreshGroupControls();
            await File.WriteAllTextAsync(Path.Combine(output, "group-failed-floor-workspace.json"), JsonSerializer.Serialize(new {
                state = page.State, session = page.SessionView,
                memberIds = members.Items.Cast<RuntimeGroupMember>().Select(row => row.MapId).ToArray(),
                selected = members.SelectedValue as string, enabled = choose.IsEnabled,
                message = Field<TextBlock>(page, "groupChoiceMessage").Text
            }, new JsonSerializerOptions { WriteIndented = true }));
            // Original public Runtime retains the registered 04/12/19 family
            // when a target-floor fit fails; failure is not a new identity rank.
            check(Field<StackPanel>(page, "groupArea").Visibility == Visibility.Visible
                && members.Items.Cast<RuntimeGroupMember>().Select(row => row.MapId).Order(StringComparer.Ordinal)
                    .SequenceEqual(new[] { "nightmare-04", "nightmare-12", "nightmare-19" })
                && members.SelectedValue as string == "nightmare-12" && choose.IsEnabled
                && page.State is { IdentityKind: "group", IdentitySource: "group-default" }
                && page.State.Context is { MapId: "nightmare-04", FloorId: "2" }
                && page.State.GroupChoice is { DesiredMapId: "nightmare-12", HeldMapId: null, Status: "awaiting-geometry" }
                && page.ReadRender() is null && page.ReadPendingGroupMaterial() is null
                && Field<TextBlock>(page, "groupChoiceMessage").Text.Contains("已保留选择"),
                "group-failed-floor-keeps-the-registered-member-list-reference-and-unconfirmed-intent-without-current-material");
            capture.Select("batch01-event-000006"); page.Dispatch("capture_now"); await page.DrainAsync(); page.RefreshGroupControls();
            check(members.SelectedValue as string == "nightmare-12" && page.ReadPendingGroupMaterial() is { MapId: "nightmare-12" }
                && page.State!.GroupChoice.HeldMapId is null,
                "group-return-retains-the-desired-control-selection-and-still-waits-for-consumer-ACK");
            await File.WriteAllTextAsync(Path.Combine(output, "group-workspace.json"), JsonSerializer.Serialize(new {
                state = page.State, session = page.SessionView, consumed,
                scope = "Real WPF group controls and Session/material-ready consumer on the independent original Runtime's glyph-ablated PNG fixture. Default untouched-frame S0 index UI is tested separately. No native input/game acceptance."
            }, new JsonSerializerOptions { WriteIndented = true }));
        }
        finally { host?.Dispose(); await page.CloseAsync(); }
    }

    static async Task Until(Func<bool> ready)
    {
        var started = System.Diagnostics.Stopwatch.StartNew();
        while (!ready())
        {
            if (started.Elapsed > TimeSpan.FromSeconds(10)) throw new TimeoutException("Group material consumer did not settle.");
            await Task.Delay(10);
        }
    }
    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;
}
