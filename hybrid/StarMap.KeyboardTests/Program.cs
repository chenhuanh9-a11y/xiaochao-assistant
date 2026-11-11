using StarMap.Windows;
using System.Reflection;

internal static class Program
{
    private static async Task<int> Main()
    {
        try
        {
            CheckReducerSequences();
            await CheckMailboxOverflow();
            await CheckNativeInstallAndStopAsync();
            Console.WriteLine("PASS keyboard source: deterministic edge sequences, bounded overflow, hook install and thread stop (StarMap pass-through keyboard hook).");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"FAIL keyboard source: {ex}");
            return 1;
        }
    }

    private static void CheckReducerSequences()
    {
        var initiallyDown = new HashSet<int> { 0x47, 0xA2 };
        var queryCount = 0;
        var state = new KeyboardEventState();
        state.Seed(vk =>
        {
            queryCount++;
            return initiallyDown.Contains(vk) ? unchecked((short)0x8000) : (short)0;
        });
        Check(queryCount == 252, $"Expected a single seed pass over all non-generic VKs; got {queryCount} reads.");
        Check(state.GetHeldKeys(new HashSet<int> { 0x47 }).SequenceEqual(new[] { 0x47, 0xA2 }),
            "Initial held snapshot did not include the subscribed key and left Ctrl.");

        var startupHeldRepeat = state.Observe(0x47, 0x22, 0, true, 99, (nint)0x1234, 9);
        var startupHeldRelease = state.Observe(0x47, 0x22, 0, false, 100, (nint)0x1234, 9);
        var first = state.Observe(0x48, 0x23, 0, true, 101, (nint)0x1234, 9);
        var repeat = state.Observe(0x48, 0x23, 0, true, 102, (nint)0x1234, 9);
        var release = state.Observe(0x48, 0x23, 0, false, 103, (nint)0x1234, 9);
        var duplicateRelease = state.Observe(0x48, 0x23, 0, false, 104, (nint)0x1234, 9);
        Check(startupHeldRepeat.WasDown && startupHeldRelease.WasDown,
            "A key held during the one-time seed was treated as a new physical press.");
        Check(!first.WasDown && repeat.WasDown && release.WasDown && !duplicateRelease.WasDown,
            "Down, repeat, up, and duplicate up state sequence was incorrect.");
        Check(first.Timestamp == 101 && first.ForegroundHwnd == (nint)0x1234 && first.Generation == 9,
            "Timestamp, foreground, or generation facts were not preserved.");

        var leftCtrl = state.Observe(0x11, 0x1D, 0, true, 110, 0, 10);
        var rightCtrl = state.Observe(0x11, 0x1D, 0x01, true, 111, 0, 10);
        var leftShift = state.Observe(0x10, 0x2A, 0, true, 112, 0, 10);
        var rightShift = state.Observe(0x10, 0x36, 0, true, 113, 0, 10);
        var leftAlt = state.Observe(0x12, 0x38, 0, true, 114, 0, 10);
        var rightAlt = state.Observe(0x12, 0x38, 0x01, true, 115, 0, 10);
        Check(leftCtrl.PhysicalVirtualKey == 0xA2 && rightCtrl.PhysicalVirtualKey == 0xA3,
            "Ctrl sides were not separated using the extended flag.");
        Check(leftShift.PhysicalVirtualKey == 0xA0 && rightShift.PhysicalVirtualKey == 0xA1,
            "Shift sides were not separated using the scan code.");
        Check(leftAlt.PhysicalVirtualKey == 0xA4 && rightAlt.PhysicalVirtualKey == 0xA5,
            "Alt sides were not separated using the extended flag.");
    }

    private static async Task CheckMailboxOverflow()
    {
        var mailbox = new BoundedKeyboardEventMailbox(1);
        var overflowCount = 0;
        var first = new KeyboardEdge(0x47, 0x47, true, false, 1, 0, 1);
        var second = new KeyboardEdge(0x47, 0x47, false, true, 2, 0, 1);
        Check(mailbox.TryWrite(first, () => overflowCount++), "Mailbox rejected its first item.");
        Check(!mailbox.TryWrite(second, () => overflowCount++), "Mailbox did not fail when capacity was exceeded.");
        Check(!mailbox.TryWrite(second, () => overflowCount++), "Mailbox accepted input after overflow.");
        Check(overflowCount == 1, "Overflow notification must happen once and close the event source path.");
        mailbox.Complete(new InvalidOperationException("overflow"));

        var read = new List<KeyboardEdge>();
        try
        {
            await foreach (var edge in mailbox.ReadAllAsync(CancellationToken.None))
                read.Add(edge);
        }
        catch (InvalidOperationException ex) when (ex.Message == "overflow")
        {
            // The queued edge remains observable, and the terminal overflow is explicit.
        }
        Check(read.Count == 1 && read[0] == first, "Overflow fabricated or silently discarded a queued edge.");
    }

    private static async Task CheckNativeInstallAndStopAsync()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("This standalone smoke check requires Windows 10 x64.");

        await using var source = new PassThroughKeyboardSource();
        source.UpdateSubscriptions(new[] { 0x47, 0x5B, 0x5C });
        var started = await source.StartAsync();
        var threadField = typeof(PassThroughKeyboardSource).GetField("_thread", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var hookThread = (Thread)threadField.GetValue(source)!;
        Check(source.IsRunning, "WH_KEYBOARD_LL was not active after StartAsync completed.");
        Check(started.Generation == source.Generation, "Start result generation did not match the running source.");

        var previousGeneration = source.Generation;
        Check(source.UpdateSubscriptions(new[] { 0x47, 0x5B, 0x5C, 0x45 }) == previousGeneration + 1,
            "Subscription update did not increment its atomic generation.");
        Check(source.IsRunning, "Updating subscriptions restarted or stopped the hook.");

        var close = await source.CloseAsync(TimeSpan.FromSeconds(3));
        Check(close.Stopped, $"Hook thread did not stop: {close.FailureMessage}");
        Check(!hookThread.IsAlive, "CloseAsync reported Stopped before the owning Thread actually exited.");
        Check(source.FailureMessage is null, $"Hook install/stop smoke reported a failure: {source.FailureMessage}");
        Check(!source.IsRunning && source.HookThreadName == "StarMap pass-through keyboard hook",
            "Dedicated hook thread was still active or had an unexpected name after close.");
        var count = 0;
        await foreach (var _ in source.ReadAllAsync())
            count++;
        Check(count == 0, "Install/stop smoke unexpectedly received keyboard events; no keys were injected.");

        await using var closedBeforeStart = new PassThroughKeyboardSource();
        Check((await closedBeforeStart.CloseAsync()).Stopped, "Close-before-start did not finish cleanly.");
        try
        {
            await closedBeforeStart.StartAsync();
            throw new InvalidOperationException("StartAsync succeeded after CloseAsync had atomically closed the source.");
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("closed keyboard source", StringComparison.Ordinal))
        {
            // Close-before-start and StartAsync share the lifecycle lock.
        }
    }

    private static void Check(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
