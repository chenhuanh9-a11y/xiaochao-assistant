using System.Windows.Threading;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;

internal static class Program
{
    static int checks;
    static readonly List<string> failures = [];
    static void Check(bool condition, string name)
    {
        checks++;
        if (condition) Console.WriteLine("ok " + name);
        else { failures.Add(name); Console.Error.WriteLine("FAIL " + name); }
    }

    [STAThread]
    static int Main()
    {
        var ui = Dispatcher.CurrentDispatcher;
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(ui));
        ui.InvokeAsync(async () =>
        {
            try { await Run(ui); }
            catch (Exception error) { Check(false, "unexpected/" + error); }
            finally { ui.BeginInvokeShutdown(DispatcherPriority.Background); }
        });
        Dispatcher.Run();
        Console.WriteLine($"{checks - failures.Count}/{checks} checks passed. Native hooks and physical input were not used.");
        return failures.Count == 0 ? 0 : 1;
    }

    static GlobalInputBindings Bind(string map = "G", params (string Action, string Key)[] overrides)
    {
        var actions = Enumerable.Repeat("", SettingsDocument.ActionOrder.Length).ToArray();
        foreach (var item in overrides) actions[Array.IndexOf(SettingsDocument.ActionOrder, item.Action)] = item.Key;
        return GlobalInputBindings.FromSettings(map, actions);
    }

    static async Task Until(Func<bool> condition, string name)
    {
        var until = DateTime.UtcNow + TimeSpan.FromSeconds(5);
        while (!condition() && DateTime.UtcNow < until) await Task.Delay(5);
        if (!condition()) throw new TimeoutException(name);
    }

    static async Task Run(Dispatcher ui)
    {
        await BasicAndStartOrdering(ui);
        await FreshScopeAndFailure(ui);
        await ModifierTaps(ui);
        await PickerAndManagement(ui);
        await ConfigureOrdering(ui);
        await LocalPanelOwnership(ui);
        await StopAndBackpressure(ui);
        await PointerContext(ui);
        await PointerIntentContext(ui);
    }

    static async Task BasicAndStartOrdering(Dispatcher ui)
    {
        var bindings = Bind();
        var source = new FixtureInputSource(bindings) { DelayStartResult = true };
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true));
        var observed = new List<WpfGlobalInputDelivery>();
        var uiThread = Environment.CurrentManagedThreadId;
        var scopeReads = 0; var wrongThread = false;
        await using var host = new WpfGlobalInputController(ui, bindings,
            () => { scopeReads++; wrongThread |= Environment.CurrentManagedThreadId != uiThread; return context; },
            delivery => { wrongThread |= Environment.CurrentManagedThreadId != uiThread; observed.Add(delivery); }, source);
        Check(source.Starts == 0 && scopeReads == 0 && !host.ListeningRequested, "constructor/does-not-listen-or-read-ui");
        await host.ConfigureAsync(bindings);
        Check(source.Starts == 0 && !host.IsReady, "idle-configure/does-not-install");
        var starting = host.StartAsync();
        source.Key(71, true);
        await Until(() => observed.Any(item => item.Notification.Key is not null), "first down UI consumption");
        Check(!starting.IsCompleted && host.IsReady && observed[0].Notification.Kind == GlobalInputNotificationKind.Started,
            "start/source-started-precedes-key-even-before-native-result");
        Check(observed.Sum(item => item.Dispatch.Actions.Count) == 1, "start/down-delivered-before-return");
        source.ReleaseStartResult();
        Check((await starting).Started, "start/waits-for-native-and-ui-ready");
        source.Key(71, true);
        source.Key(71, false);
        source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 4, "repeat and release sequence");
        Check(observed.Sum(item => item.Dispatch.Actions.Count) == 2, "start/returned-snapshot-does-not-reset-delivered-down");
        Check(!wrongThread && observed.All(item => item.Notification.Key is not { Consumed: true }), "delivery/real-ui-owner-and-pass-through");
        source.NowSeconds = 103;
        source.Key(71, false, created: 100);
        source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 6, "stale release");
        Check(observed.Sum(item => item.Dispatch.Actions.Count) == 3, "release/stale-edge-clears-held-before-fresh-down");
        source.End("fixture source failure");
        await source.Drained;
        Check(observed.TakeLast(2).Select(item => item.Notification.Kind).SequenceEqual(
            new[] { GlobalInputNotificationKind.Failed, GlobalInputNotificationKind.Stopped })
            && !host.IsReady && !host.ListeningRequested, "termination/failed-stopped-and-ui-reset-in-source-order");
        source.DelayStartResult = false;
        await host.StartAsync(); // The physical G is still held, and must seed the replacement.
        var before = observed.Sum(item => item.Dispatch.Actions.Count);
        source.Key(71, true); source.Key(71, false); source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 9, "restart seed");
        Check(observed.Sum(item => item.Dispatch.Actions.Count) == before + 1, "restart/physical-held-seed-no-invented-new-press");
    }

    static async Task FreshScopeAndFailure(Dispatcher ui)
    {
        var bindings = Bind(); var source = new FixtureInputSource(bindings);
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true), BindingGeneration: 1);
        var observed = new List<WpfGlobalInputDelivery>(); var failScope = false;
        await using var host = new WpfGlobalInputController(ui, bindings,
            () => failScope ? throw new InvalidOperationException("fixture scope unavailable") : context, observed.Add, source);
        await host.StartAsync();
        source.Key(71, true, created: 100);
        source.NowSeconds = 100.1;
        context = context with { BindingGeneration = 2 }; host.RefreshScope();
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 1, "rebind pending key");
        Check(observed[^1].Dispatch.Actions.Count == 0, "binding/reused-hwnd-rejects-queued-old-press");
        source.Key(71, false); source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 3, "fresh rebound key");
        Check(observed[^1].Dispatch.Actions.SequenceEqual(new[] { "map" }), "binding/new-edge-on-current-binding-allowed");
        source.Key(71, false);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 4, "release before bad scope");
        failScope = true; source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 5, "scope read failure");
        failScope = false; source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 6, "repeat after scope recovers");
        Check(observed.TakeLast(2).All(item => item.Dispatch.Actions.Count == 0)
            && host.LastDeliveryError?.Contains("scope unavailable", StringComparison.Ordinal) == true,
            "scope/read-failure-still-tracks-down-and-rejects-repeat");
        source.Key(71, false); source.Key(71, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 8, "scope recovery fresh key");
        Check(observed[^1].Dispatch.Actions.Count == 1, "scope/fresh-press-recovers-without-reinstall");
        source.Key(71, false); source.Key(71, true);
        source.NowSeconds = 100.2;
        context = context with { Scope = context.Scope with { TextEntryActive = true } }; host.RefreshScope();
        context = context with { Scope = context.Scope with { TextEntryActive = false } }; host.RefreshScope();
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 10, "typing guard transition");
        Check(observed[^1].Dispatch.Actions.Count == 0, "text/queued-press-cannot-become-new-shortcut-after-focus-exits");
        source.Key(71, false); source.Key(71, true, foreground: 99);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 12, "foreground scope");
        Check(observed[^1].Dispatch.Actions.Count == 0, "foreground/other-application-never-routes-game-action");
    }

    static async Task ModifierTaps(Dispatcher ui)
    {
        var bindings = Bind("Shift"); var source = new FixtureInputSource(bindings);
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true));
        var observed = new List<WpfGlobalInputDelivery>();
        await using var host = new WpfGlobalInputController(ui, bindings, () => context, observed.Add, source);
        await host.StartAsync();
        source.Key(0x10, true, scan: 0x2a);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 1, "modifier press");
        Check(observed[^1].Dispatch.Actions.Count == 0, "modifier/press-is-not-a-tap");
        var cancels = source.TapCancellations;
        context = context with { Scope = context.Scope with { EditingBindings = true } }; host.RefreshScope();
        context = context with { Scope = context.Scope with { EditingBindings = false } }; host.RefreshScope();
        source.NowSeconds = 100.1;
        var release = source.Key(0x10, false, scan: 0x2a);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 2, "canceled modifier release");
        Check(source.TapCancellations >= cancels + 2 && release?.ModifierTap == false && observed[^1].Dispatch.Actions.Count == 0,
            "modifier/editing-transition-cancels-native-and-subscriber-tap");
        source.Key(0x10, true, scan: 0x2a); source.NowSeconds = 100.2;
        source.Key(0x10, false, scan: 0x2a);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 4, "clean modifier tap");
        Check(observed[^1].Dispatch.Actions.SequenceEqual(new[] { "map" }), "modifier/fresh-clean-tap-still-works");
    }

    static async Task PickerAndManagement(Dispatcher ui)
    {
        var bindings = Bind("G", ("candidate-next", "F1"), ("toggle-panel", "F9"), ("start-round", "F10"));
        var source = new FixtureInputSource(bindings);
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true), new[] { "F1", "Escape" });
        var observed = new List<WpfGlobalInputDelivery>();
        await using var host = new WpfGlobalInputController(ui, bindings, () => context, observed.Add, source);
        await host.StartAsync();
        source.Key(0x70, true); source.Key(0x1b, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 2, "picker keys");
        Check(observed[^2].Dispatch is { PickerKey: "F1", Actions.Count: 0 }, "picker/visible-key-owns-bare-primary");
        Check(observed[^1].Dispatch is { PickerKey: "Escape" } && observed[^1].Dispatch.Actions.SequenceEqual(new[] { "escape" }),
            "picker/escape-preserves-existing-close-action");
        context = context with { Scope = context.Scope with { GameWindow = 0, GameWindowExists = false,
            GameEnabled = false, CurrentForegroundWindow = 99 } }; host.RefreshScope();
        source.Key(0x79, true, foreground: 99);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 3, "management without game");
        Check(observed[^1].Dispatch.Actions.SequenceEqual(new[] { "start-round" }), "management/explicit-intent-survives-missing-game");
        context = context with { Scope = context.Scope with { CurrentForegroundWindow = 77, ForegroundIsAssistant = true } };
        host.RefreshScope(); source.Key(0x78, true, foreground: 77);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 4, "assistant panel owner");
        Check(observed[^1].Dispatch.Actions.Count == 0, "management/assistant-F9-keeps-app-local-owner");
        source.Key(0x70, false, foreground: 77); source.Key(0x11, true, foreground: 77);
        source.Key(0x70, true, foreground: 77);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 7, "picker ctrl chord");
        Check(observed[^1].Dispatch.PickerKey is null, "picker/modifier-chord-never-selects");
    }

    static async Task ConfigureOrdering(Dispatcher ui)
    {
        var bindings = Bind("G", ("toggle-panel", "F9"));
        var nextBindings = Bind("H", ("toggle-panel", "F12"));
        var source = new FixtureInputSource(bindings) { DelayConfigurationResult = true };
        var observed = new List<WpfGlobalInputDelivery>();
        await using var host = new WpfGlobalInputController(ui, bindings,
            () => new(new(42, true, 42, false, true, true)), observed.Add, source);
        await host.StartAsync();
        var oldGeneration = source.Generation;
        var old = source.Key(71, true)!;
        var configuring = host.ConfigureAsync(nextBindings);
        Check(!host.IsReady && ReferenceEquals(host.ActiveBindings, bindings), "configure/suspends-local-owner-until-source-started");
        source.Key(72, true);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 2, "configured new key before return");
        Check(observed.Where(item => item.Notification.Key?.VirtualKey == 71).Single().Dispatch.Actions.Count == 0,
            "configure/queued-old-generation-actions-suspended");
        Check(!configuring.IsCompleted && observed[^1].Dispatch.Actions.SequenceEqual(new[] { "map" }),
            "configure/ordered-started-applies-new-binding-before-native-result");
        Check(host.IsReady && ReferenceEquals(host.ActiveBindings, nextBindings)
            && host.ActiveBindings.Items.Single(item => item.Action == "toggle-panel").VirtualKey == 0x7b,
            "configure/active-panel-owner-switches-at-started-before-native-result");
        source.ReleaseConfigurationResult();
        var snapshot = await configuring;
        Check(snapshot.Generation == host.Generation && host.Generation > oldGeneration, "configure/return-matches-consumed-started-generation");
        source.Key(72, true);
        source.Post(new(GlobalInputNotificationKind.Key, oldGeneration, Key: old with { IsDown = false }));
        source.Post(new(GlobalInputNotificationKind.Started, snapshot.Generation, Snapshot: snapshot));
        source.Key(72, true);
        await Until(() => observed.Count(item => item.Notification.Key?.Generation == snapshot.Generation) == 3, "repeat after config return");
        Check(ReferenceEquals(host.ActiveBindings, nextBindings) && observed.Sum(item => item.Dispatch.Actions.Count) == 1,
            "configure/no-return-reset-no-duplicate-started-reset-no-old-edge-revival");
        source.Key(72, false); source.Key(72, true);
        await Until(() => observed.Count(item => item.Notification.Key?.Generation == snapshot.Generation) == 5, "new config fresh key");
        Check(observed[^1].Dispatch.Actions.Count == 1, "configure/release-rearms-current-binding");
    }

    static async Task LocalPanelOwnership(Dispatcher ui)
    {
        var bindings = Bind("G", ("toggle-panel", "F9"));
        var source = new FixtureInputSource(bindings);
        // The WPF handler already hid HWND 91 before the queued native edge is consumed.
        var observed = new List<WpfGlobalInputDelivery>();
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true), LocalPanelWindow: 91);
        await using var host = new WpfGlobalInputController(ui, bindings, () => context, observed.Add, source);
        await host.StartAsync();
        source.Key(0x78, true, foreground: 91); source.Key(0x78, false, foreground: 91);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 2, "local owner after hide");
        Check(observed.All(item => item.Dispatch.Actions.Count == 0), "panel/local-first-hide-keeps-original-HWND-owner-for-queued-native-edge");
        source.Key(0x78, true, foreground: 42); source.Key(0x78, false, foreground: 42);
        await Until(() => observed.Count(item => item.Notification.Key is not null) == 4, "game panel key");
        Check(observed.Sum(item => item.Dispatch.Actions.Count) == 1, "panel/game-key-retains-one-global-owner");
    }

    static async Task StopAndBackpressure(Dispatcher ui)
    {
        var bindings = Bind(); var source = new FixtureInputSource(bindings);
        var observed = new List<WpfGlobalInputDelivery>();
        var host = new WpfGlobalInputController(ui, bindings,
            () => new(new(42, true, 42, false, true, true)), observed.Add, source);
        await host.StartAsync();
        source.Key(71, true);
        Check(SpinWait.SpinUntil(() => source.InKeySubscriber, TimeSpan.FromSeconds(2)), "stop/source-waits-for-one-pending-ui-operation");
        // Deliberately do not pump the UI. Cancellation must abort its pending operation.
        var stop = host.StopAsync();
        var completed = stop.Wait(TimeSpan.FromSeconds(3));
        Check(completed && stop.Result.Stopped && observed.All(item => item.Notification.Key is null),
            "stop/cancels-pending-ui-operation-without-ui-deadlock");
        await host.DisposeAsync();

        source = new FixtureInputSource(bindings); observed = [];
        Task<GlobalInputStopResult>? stopInsideCallback = null;
        var sawRunning = false;
        WpfGlobalInputController? running = null;
        await using (running = new(ui, bindings, () => new(new(42, true, 42, false, true, true)), item =>
        {
            observed.Add(item);
            if (item.Dispatch.Actions.Count == 0) return;
            stopInsideCallback = running!.StopAsync();
            sawRunning = !stopInsideCallback.IsCompleted;
        }, source))
        {
            await running.StartAsync(); source.Key(71, true);
            await Until(() => stopInsideCallback is not null, "stop inside callback");
            Check(sawRunning, "stop/running-business-callback-is-not-falsely-drained");
            Check((await stopInsideCallback!).Stopped && source.Drained.IsCompleted,
                "stop/completes-only-after-running-callback-and-real-source-dispatch-return");
        }

        source = new FixtureInputSource(bindings); observed = [];
        await using var bounded = new WpfGlobalInputController(ui, bindings,
            () => new(new(42, true, 42, false, true, true)), observed.Add, source);
        await bounded.StartAsync(); source.Key(71, true);
        Check(SpinWait.SpinUntil(() => source.InKeySubscriber, TimeSpan.FromSeconds(2)), "bounded/source-blocks-at-ui-boundary");
        var item = new GlobalInputNotification(GlobalInputNotificationKind.Warning, source.Generation, Code: "fixture-full");
        var filled = true;
        for (var index = 0; index < WindowsGlobalInputDelivery.Capacity; index++) filled &= source.TryPost(item);
        Check(filled && !source.TryPost(item), "bounded/real-source-channel-exact-capacity-no-ui-begininvoke-flood");
        // Native configuration resets state before Queue(Started). With the actual channel
        // full that Started is lost; the out-of-band terminal carries its newer generation.
        var lostConfiguration = bounded.ConfigureAsync(Bind("H"));
        await source.Drained;
        Check(observed.Count(delivery => delivery.Notification.Code == "fixture-full") == WindowsGlobalInputDelivery.Capacity
            && observed.TakeLast(2).Select(delivery => delivery.Notification.Kind).SequenceEqual(
                new[] { GlobalInputNotificationKind.Failed, GlobalInputNotificationKind.Stopped }),
            "bounded/ordered-channel-and-out-of-band-terminal-drain-through-real-host");
        Check(!bounded.IsReady && observed[^1].Notification.Snapshot?.Held.Count == 0
            && bounded.Generation == source.Generation,
            "bounded/newer-generation-terminal-reset-survives-lost-configure-started");
        var canceled = false;
        try { await lostConfiguration; } catch (OperationCanceledException) { canceled = true; }
        Check(canceled, "bounded/lost-configuration-does-not-report-ui-ready");
        await bounded.StartAsync();
        source.Key(72, true);
        await Until(() => observed.Any(delivery => delivery.Notification.Key?.VirtualKey == 72), "restart after lost configuration");
        Check(observed[^1].Dispatch.Actions.SequenceEqual(new[] { "map" }),
            "bounded/restart-retains-desired-bindings-after-lost-started");
    }

    static async Task PointerContext(Dispatcher ui)
    {
        var bindings = Bind(); var source = new FixtureInputSource(bindings);
        var context = new WpfGlobalInputContext(new(42, true, 42, false, true, true), BindingGeneration: 1,
            PointerTarget: new(42, "frame-a", 100));
        var observed = new List<WpfGlobalInputDelivery>();
        await using var host = new WpfGlobalInputController(ui, bindings, () => context, observed.Add, source);
        await host.StartAsync();
        var down = new GlobalInputPointerEvent("down", 10, 20, 100, 42, 42, source.Generation, 1,
            ContextId: "frame-a", Sequence: 1, GestureId: 1);
        source.Post(new(GlobalInputNotificationKind.Pointer, source.Generation, Pointer: down));
        await Until(() => observed.Any(item => item.Pointer?.Phase == "down"), "pointer down");
        Check(source.PointerTarget?.ContextId == "frame-a", "pointer/target-is-explicit-cached-context");
        context = context with { PointerTarget = new(42, "frame-b", 100) }; host.RefreshScope();
        Check(observed[^1].Pointer is { Phase: "cancel", ContextId: "frame-a" }, "pointer/context-change-cancels-delivered-gesture");
        source.Post(new(GlobalInputNotificationKind.Pointer, source.Generation,
            Pointer: down with { Phase = "up", Sequence = 2 }));
        await Until(() => observed.Any(item => item.Notification.Pointer?.Phase == "up"), "old pointer up");
        Check(observed[^1].Pointer is null, "pointer/old-context-release-not-authorized-for-new-frame");
    }

    sealed record IntentContext(int Observation):IGlobalInputPointerIntentContext;
    static async Task PointerIntentContext(Dispatcher ui)
    {
        var bindings=Bind();var source=new FixtureInputSource(bindings);var slot=new GlobalInputPointerContextSlot();
        var original=new GlobalInputPointerContextSnapshot("intent-a",100,"scope-a",new IntentContext(1));slot.Publish(original);
        var context=new WpfGlobalInputContext(new(42,true,42,false,true,true),BindingGeneration:1,
            PointerTarget:new(42,"static-fallback-is-not-an-observation",0,slot),AllowPointerIntentPress:true);
        var observed=new List<WpfGlobalInputDelivery>();
        await using var host=new WpfGlobalInputController(ui,bindings,()=>context,observed.Add,source);await host.StartAsync();
        var down=new GlobalInputPointerEvent("down",10,20,100.005,42,42,source.Generation,1,
            ContextId:original.ContextId,Sequence:1,GestureId:1,PressContextSnapshot:original);
        slot.Publish(new("intent-b",100.01,"scope-a",new IntentContext(2)));source.NowSeconds=100.02;
        source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:down));
        await Until(()=>observed.Any(item=>ReferenceEquals(item.Notification.Pointer,down)),"queued intent press");
        Check(observed[^1].Pointer is null && observed[^1].PointerIntentPress==down,"pointer/advanced-observation-only-reaches-opted-in-intent-channel");
        slot.Publish(new("intent-c",100.03,"scope-b",new IntentContext(3)));source.NowSeconds=100.04;
        var stale=down with {Sequence=2,GestureId=2};source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:stale));
        await Until(()=>observed.Any(item=>ReferenceEquals(item.Notification.Pointer,stale)),"hard scope replacement");
        Check(observed[^1].Pointer is null && observed[^1].PointerIntentPress is null,"pointer/hard-scope-replacement-rejects-old-intent-context");
        slot.Publish(null);var noObservation=down with {Sequence=3,GestureId=3};source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:noObservation));
        await Until(()=>observed.Any(item=>ReferenceEquals(item.Notification.Pointer,noObservation)),"empty pointer slot");
        Check(observed[^1].Pointer is null && observed[^1].PointerIntentPress is null && source.PointerTarget?.ReadContext() is null,
            "pointer/null-slot-does-not-fallback-to-static-context");
        slot.Publish(new("intent-d",100.01,"scope-a",new IntentContext(4)));context=context with {AllowPointerIntentPress=false};
        var disabled=down with {Sequence=4,GestureId=4};source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:disabled));
        await Until(()=>observed.Any(item=>ReferenceEquals(item.Notification.Pointer,disabled)),"non-opted intent");
        Check(observed[^1].Pointer is null && observed[^1].PointerIntentPress is null,"pointer/default-strict-consumer-does-not-inherit-intent-permission");
    }
}
