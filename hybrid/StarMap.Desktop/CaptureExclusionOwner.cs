using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using StarMap.Windows;

namespace StarMap.Desktop;

/// <summary>UI owner of temporary affinity changes for the explicit screen-DC fallback.</summary>
public sealed class CaptureExclusionOwner(Dispatcher dispatcher, Func<bool> isClosing)
{
    private readonly HashSet<Window> registered = [];
    private readonly SemaphoreSlim lane = new(1, 1);
    private Transaction? active;
    private Task? pending;

    public void Register(Window window)
    {
        dispatcher.VerifyAccess();
        if (!registered.Add(window)) return;
        window.SourceInitialized += (_, _) => EnrollIfActive(window);
        window.Closed += (_, _) => registered.Remove(window);
        EnrollIfActive(window);
    }

    private void EnrollIfActive(Window window)
    {
        if (active is not { } transaction) return;
        try { Enroll(window, transaction); }
        catch (Exception error) { transaction.Failure = error; }
    }

    public async Task<CapturedClientFrame> CaptureAsync(WindowSnapshot target, CancellationToken cancellation)
    {
        await lane.WaitAsync(cancellation).ConfigureAwait(false);
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        pending = completion.Task;
        var transaction = new Transaction();
        Task<CapturedClientFrame>? acquisition = null;
        try
        {
            await dispatcher.InvokeAsync(() =>
            {
                cancellation.ThrowIfCancellationRequested();
                if (isClosing()) throw new OperationCanceledException("Capture owner is closing.");
                if (Environment.OSVersion.Version.Build < 19041)
                    throw Unsupported("windows-build-too-old");
                active = transaction;
                foreach (Window window in Application.Current.Windows) Register(window);
                foreach (var window in registered.ToArray()) Enroll(window, transaction);
                Flush();
            }, DispatcherPriority.Send, cancellation);
            cancellation.ThrowIfCancellationRequested();
            acquisition = Task.Run(() => WindowCapture.CaptureScreenClient(target, cancellation), cancellation);
            var pixels = await acquisition.WaitAsync(cancellation).ConfigureAwait(false);
            cancellation.ThrowIfCancellationRequested();
            if (transaction.Failure is not null) throw transaction.Failure;
            return pixels;
        }
        finally
        {
            try
            {
                // Restoration is accepted work, independent of the now-cancelled
                // request. The shell drains this owner before destroying windows.
                await dispatcher.InvokeAsync(() => Restore(transaction), DispatcherPriority.Send);
            }
            finally
            {
                // A cancelled await cannot interrupt BitBlt. Keep the single
                // acquisition lane and drain alive until its native owner exits.
                _ = FinishAcquisitionAsync(acquisition, completion);
            }
        }
    }

    public Task DrainAsync() => pending?.WaitAsync(TimeSpan.FromSeconds(2)) ?? Task.CompletedTask;

    private async Task FinishAcquisitionAsync(Task<CapturedClientFrame>? acquisition, TaskCompletionSource completion)
    {
        try { if (acquisition is not null) await acquisition.ConfigureAwait(false); }
        catch (Exception) { /* The request already observes its outcome; this lane owns cleanup. */ }
        finally { completion.TrySetResult(); lane.Release(); }
    }

    private void Enroll(Window window, Transaction transaction)
    {
        dispatcher.VerifyAccess();
        var hwnd = new WindowInteropHelper(window).Handle;
        if (hwnd == 0 || transaction.Changed.Any(row => row.Window == window && row.Hwnd == hwnd)) return;
        if (!IsWindow(hwnd) || GetWindowThreadProcessId(hwnd, out var pid) == 0 || pid != Environment.ProcessId)
            throw Unsupported("surface-ownership-unavailable");
        if (!GetWindowDisplayAffinity(hwnd, out var previous)) throw Unsupported("display-affinity-query-failed");
        if (!SetWindowDisplayAffinity(hwnd, 0x11)) throw Unsupported("display-affinity-set-failed");
        transaction.Changed.Add(new(window, hwnd, previous));
        if (!GetWindowDisplayAffinity(hwnd, out var actual) || actual != 0x11)
            throw Unsupported("display-affinity-not-excluded");
        Flush();
    }

    private void Restore(Transaction transaction)
    {
        dispatcher.VerifyAccess();
        var failures = new List<Exception>();
        foreach (var row in transaction.Changed.AsEnumerable().Reverse())
        {
            if (!registered.Contains(row.Window) || new WindowInteropHelper(row.Window).Handle != row.Hwnd || !IsWindow(row.Hwnd)) continue;
            try
            {
                if (GetWindowThreadProcessId(row.Hwnd, out var pid) == 0 || pid != Environment.ProcessId) continue;
                if (!SetWindowDisplayAffinity(row.Hwnd, row.Previous)) throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            catch (Exception error) { failures.Add(error); }
        }
        try { if (transaction.Changed.Count > 0) Flush(); }
        catch (Exception error) { failures.Add(error); }
        if (ReferenceEquals(active, transaction)) active = null;
        if (failures.Count > 0) throw new AggregateException("Capture surface affinity could not be restored.", failures);
    }

    private static void Flush()
    {
        if (DwmFlush() < 0) throw Unsupported("desktop-composition-flush-failed");
    }
    private static NativeCallbackException Unsupported(string reason) => new("capture-safe-fallback-unsupported", reason,
        new { safeFallback = "unsupported", reason, windowsBuild = Environment.OSVersion.Version.Build,
            minimumWindowsBuild = 19041, exclusionMode = "WDA_EXCLUDEFROMCAPTURE" });
    private sealed class Transaction
    {
        public List<Surface> Changed { get; } = [];
        public volatile Exception? Failure;
    }
    private sealed record Surface(Window Window, nint Hwnd, uint Previous);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsWindow(nint hwnd);
    [DllImport("user32.dll", SetLastError = true)] private static extern uint GetWindowThreadProcessId(nint hwnd, out uint pid);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetWindowDisplayAffinity(nint hwnd, out uint affinity);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetWindowDisplayAffinity(nint hwnd, uint affinity);
    [DllImport("dwmapi.dll")] private static extern int DwmFlush();
}
