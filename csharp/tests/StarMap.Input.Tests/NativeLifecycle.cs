using StarMap.App.Platform;
using StarMap.Core.Input;

/// <summary>Opt-in installation/release fixture. Not run by the default offline regression.</summary>
internal static class NativeLifecycle
{
    internal static async Task<int> RunAsync()
    {
        if (!OperatingSystem.IsWindows())
        {
            Console.Error.WriteLine("Native lifecycle fixture requires Windows; nothing installed.");
            return 2;
        }
        var actions = Enumerable.Repeat("", 10).ToArray();
        var bindings = GlobalInputBindings.FromSettings("", actions);
        await using var source = new WindowsGlobalInput(bindings);
        var notifications = new System.Collections.Concurrent.ConcurrentQueue<GlobalInputNotification>();
        source.ObservationReceived += notifications.Enqueue;
        var failed = false;
        for (var cycle = 0; cycle < 3; cycle++)
        {
            var started = await source.StartAsync();
            var stopped = await source.StopAsync();
            var ok = started.Started && started.KeyboardAvailable && started.MouseAvailable && stopped.Stopped && stopped.Error is null;
            Console.WriteLine($"cycle={cycle} keyboard={started.KeyboardAvailable} rawMouse={started.MouseAvailable} generation={started.Generation} stopped={stopped.Stopped} error={stopped.Error ?? started.Error ?? "none"}");
            failed |= !ok;
            if (!stopped.Stopped) break;
        }
        if (!failed)
        {
            // Stop may race initial queue creation; either installation result is legitimate,
            // but the owner thread, raw registration and dispatcher must finish normally.
            var starting = source.StartAsync();
            var stopping = await source.StopAsync();
            var start = await starting;
            Console.WriteLine($"stop-during-start started={start.Started} stopped={stopping.Stopped} error={stopping.Error ?? "none"}");
            failed |= !stopping.Stopped || stopping.Error is not null || source.IsRunning;
        }
        // Do not report pressed key identities or pointer contents. No action dispatcher is attached.
        Console.WriteLine($"notifications={notifications.Count}; installation/release only; no SendInput, keyboard/mouse injection, activation, game or pass-through-event acceptance.");
        return failed ? 1 : 0;
    }
}
