using System.Threading.Channels;
using StarMap.Core.Input;

namespace StarMap.App.Platform;

/// <summary>
/// One run's bounded nonblocking data path and its single out-of-band termination slot.
/// Only the native owner posts/completes; only the serial dispatcher reads/publishes.
/// A full input channel cannot prevent termination from reaching a recovering subscriber.
/// </summary>
internal sealed class WindowsGlobalInputDelivery
{
    internal const int Capacity = 1024;
    readonly Channel<GlobalInputNotification> events = Channel.CreateBounded<GlobalInputNotification>(
        new BoundedChannelOptions(Capacity) { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    readonly TaskCompletionSource<Termination> termination = new(TaskCreationOptions.RunContinuationsAsynchronously);
    sealed record Termination(long Generation, string? Error);

    internal bool TryPost(GlobalInputNotification item) => events.Writer.TryWrite(item);

    internal void Complete(long generation, string? error)
    {
        // A fixed single slot, not an extra event queue. Publish it before ending the data stream.
        if (!termination.TrySetResult(new(generation, error))) return;
        events.Writer.TryComplete();
    }

    internal async Task DispatchAsync(Action<GlobalInputNotification> publish)
    {
        GlobalInputPointerEvent? deliveredGesture = null;
        await foreach (var item in events.Reader.ReadAllAsync().ConfigureAwait(false))
        {
            if (item.Pointer is { } pointer)
            {
                if (pointer.Phase is "down" or "move") deliveredGesture = pointer;
                else if (pointer.Phase is "up" or "cancel") deliveredGesture = null;
            }
            publish(item);
        }
        var stopped = await termination.Task.ConfigureAwait(false);
        // Producer state may already be cleared by a cancel that failed TryPost. End the gesture
        // this subscriber stream actually saw, after all its accepted moves and before Stopped.
        if (deliveredGesture is { } gesture)
        {
            var cancel = gesture with { Phase = "cancel", Reason = "pointer-source-stopped" };
            publish(new(GlobalInputNotificationKind.Pointer, gesture.Generation, Pointer: cancel));
        }
        if (stopped.Error is not null)
            publish(new(GlobalInputNotificationKind.Failed, stopped.Generation,
                Code: "native-input-failed", Message: stopped.Error));
        // A lost release must not leave subscriber held/tap state alive. This is a reset snapshot,
        // never an observation that the user physically released a key or authorization to act.
        publish(new(GlobalInputNotificationKind.Stopped, stopped.Generation,
            Snapshot: new(stopped.Generation, Array.Empty<int>()), Code: "input-state-reset", Message: stopped.Error));
    }
}
