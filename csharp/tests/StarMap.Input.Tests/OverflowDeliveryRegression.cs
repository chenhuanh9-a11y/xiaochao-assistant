using StarMap.App.Platform;
using StarMap.Core.Input;

/// <summary>
/// The real production Channel and dispatcher, with a deliberately blocked subscriber.
/// Only native installation/event production/cleanup completion are replaced; no hook or OS input.
/// </summary>
internal static class OverflowDeliveryRegression
{
    internal static async Task RunAsync(Action<bool, string> check)
    {
        var bindings = GlobalInputBindings.FromSettings("G", Enumerable.Repeat("", 10).ToArray());
        var dispatcher = new GlobalInputDispatcher(bindings);
        dispatcher.Reset(bindings, new(77, Array.Empty<int>()));
        var scope = new GlobalInputScope(42, true, 42, false, true, true);
        var keyDown = new GlobalInputKeyEvent(71, 71, true, Array.AsReadOnly(new[] { 71 }), 99, 42, 77, false, false);
        var pointerDown = new GlobalInputPointerEvent("down", 10, 20, 99, 42, 42, 77, 3,
            ContextId: "synthetic-context", Sequence: 1, GestureId: 1);
        var delivery = new WindowsGlobalInputDelivery();
        var blocked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using var resume = new ManualResetEventSlim(false);
        var observed = new List<GlobalInputNotification>();
        var actions = 0;
        var activeGesture = false;
        var subscriberTimedOut = false;
        var resetApplied = false;
        var dispatch = Task.Run(() => delivery.DispatchAsync(item =>
        {
            observed.Add(item);
            if (item.Key is { } key) actions += dispatcher.Dispatch(key, scope, 100).Actions.Count;
            if (item.Pointer is { } pointer)
            {
                activeGesture = pointer.Phase is "down" or "move";
                if (pointer.Phase == "down")
                {
                    blocked.TrySetResult();
                    // Real synchronous subscriber boundary; the producer must remain nonblocking.
                    if (!resume.Wait(TimeSpan.FromSeconds(10))) subscriberTimedOut = true;
                }
            }
            if (item.Kind == GlobalInputNotificationKind.Stopped && item.Snapshot is { } snapshot)
            {
                dispatcher.Reset(bindings, snapshot);
                resetApplied = true;
            }
        }));
        check(delivery.TryPost(new(GlobalInputNotificationKind.Key, 77, Key: keyDown)), "overflow/key-down-accepted");
        check(delivery.TryPost(new(GlobalInputNotificationKind.Pointer, 77, Pointer: pointerDown)), "overflow/pointer-down-accepted");
        Task<(bool Filled, bool LostRelease, bool LostCancel, bool LostFailed, bool LostStopped)>? producer = null;
        const string error = "fixture notification overflow";
        try
        {
            await blocked.Task.WaitAsync(TimeSpan.FromSeconds(5));
            producer = Task.Run(() =>
            {
                var filled = true;
                for (var index = 0; index < WindowsGlobalInputDelivery.Capacity; index++)
                    filled &= delivery.TryPost(new(GlobalInputNotificationKind.Pointer, 77,
                        Pointer: pointerDown with { Phase = "move", X = 11 + index, Sequence = 2 + index }));
                var lostRelease = !delivery.TryPost(new(GlobalInputNotificationKind.Key, 77,
                    Key: keyDown with { IsDown = false, Held = Array.Empty<int>() }));
                // Reproduce why the former same-channel terminal path cannot repair saturation.
                var lostCancel = !delivery.TryPost(new(GlobalInputNotificationKind.Pointer, 77,
                    Pointer: pointerDown with { Phase = "cancel" }));
                var lostFailed = !delivery.TryPost(new(GlobalInputNotificationKind.Failed, 77, Message: error));
                var lostStopped = !delivery.TryPost(new(GlobalInputNotificationKind.Stopped, 77));
                // Exactly the production native-finally handoff, with OS cleanup replaced.
                delivery.Complete(77, error);
                delivery.Complete(77, "a duplicate completion must not replace the first");
                return (Filled: filled, LostRelease: lostRelease, LostCancel: lostCancel,
                    LostFailed: lostFailed, LostStopped: lostStopped);
            });
            var result = await producer.WaitAsync(TimeSpan.FromSeconds(5));
            check(result.Filled, "overflow/actual-1024-channel-filled");
            check(result.LostRelease && result.LostCancel && result.LostFailed && result.LostStopped,
                "overflow/same-channel-release-and-terminal-writes-rejected");
            check(!dispatch.IsCompleted, "overflow/producer-completes-while-subscriber-blocked");
        }
        catch (TimeoutException) { check(false, "overflow/nonblocking-producer-and-subscriber-entry"); }
        finally { resume.Set(); }
        if (producer is not null) await producer.WaitAsync(TimeSpan.FromSeconds(5));
        // Complete even after a failed arrangement, so the fixture never leaves its own task alive.
        delivery.Complete(77, error);
        await dispatch.WaitAsync(TimeSpan.FromSeconds(5));
        check(!subscriberTimedOut, "overflow/subscriber-released-by-fixture");
        check(observed.Count == WindowsGlobalInputDelivery.Capacity + 5, "overflow/bounded-data-plus-three-terminal-notifications");
        var terminal = observed.TakeLast(3).ToArray();
        check(terminal.Length == 3 && terminal[0].Pointer is { Phase: "cancel", GestureId: 1, PointerGeneration: 3 }
            && terminal[0].Pointer!.ContextId == "synthetic-context"
            && terminal[1].Kind == GlobalInputNotificationKind.Failed && terminal[1].Message == error
            && terminal[2].Kind == GlobalInputNotificationKind.Stopped,
            "overflow/visible-gesture-cancel-then-failed-then-stopped");
        check(observed.Count(item => item.Kind == GlobalInputNotificationKind.Failed) == 1
            && observed.Count(item => item.Kind == GlobalInputNotificationKind.Stopped) == 1
            && observed.Count(item => item.Pointer?.Phase == "cancel") == 1,
            "overflow/terminal-delivery-exactly-once");
        check(!activeGesture, "overflow/subscriber-gesture-ended");
        check(resetApplied && terminal[^1].Snapshot is { Generation: 77, Held.Count: 0 }
            && terminal[^1].Code == "input-state-reset", "overflow/explicit-empty-held-reset-delivered");
        check(actions == 1, "overflow/termination-does-not-inject-physical-release-or-action");
        check(dispatcher.Dispatch(keyDown, scope, 100).Actions.SequenceEqual(new[] { "map" }),
            "overflow/lost-release-held-state-cleared-by-stopped-snapshot");

        // The normal up/cancel path must not acquire a duplicate synthetic cancellation.
        var ended = new WindowsGlobalInputDelivery();
        var normal = new List<GlobalInputNotification>();
        check(ended.TryPost(new(GlobalInputNotificationKind.Pointer, 77, Pointer: pointerDown))
            && ended.TryPost(new(GlobalInputNotificationKind.Pointer, 77, Pointer: pointerDown with { Phase = "up" })),
            "normal-stop/completed-gesture-enqueued");
        ended.Complete(77, null);
        await ended.DispatchAsync(normal.Add);
        check(normal.Count(item => item.Pointer?.Phase == "cancel") == 0
            && normal.Count(item => item.Kind == GlobalInputNotificationKind.Failed) == 0
            && normal.Count(item => item.Kind == GlobalInputNotificationKind.Stopped) == 1,
            "normal-stop/no-duplicate-gesture-cancel-or-failure");
        check(normal[^1].Snapshot is { Generation: 77, Held.Count: 0 }, "normal-stop/empty-held-reset-always-present");
    }
}
