using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading.Channels;

namespace StarMap.Windows;

/// <summary>
/// Pass-through, event-driven keyboard facts from a dedicated WH_KEYBOARD_LL thread.
/// This primitive does not implement shortcuts, modifier taps, mouse input, or GUI work.
/// </summary>
public sealed class PassThroughKeyboardSource : IAsyncDisposable
{
    private const int WhKeyboardLl = 13;
    private const uint WmQuit = 0x0012;
    private const uint PmNoRemove = 0x0000;
    private const int WmKeyDown = 0x0100;
    private const int WmKeyUp = 0x0101;
    private const int WmSysKeyDown = 0x0104;
    private const int WmSysKeyUp = 0x0105;
    private const uint LlkhfUp = 0x80;
    private static readonly TimeSpan DefaultCloseTimeout = TimeSpan.FromSeconds(2);

    private readonly object _lifecycleLock = new();
    private readonly BoundedKeyboardEventMailbox _mailbox;
    private readonly TaskCompletionSource _queueReady = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly TaskCompletionSource _threadStopped = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly TaskCompletionSource<KeyboardStartInfo> _startCompletion = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private SubscriptionSnapshot _subscriptions = new(0, new HashSet<int>());
    private Thread? _thread;
    private NativeMethodsForKeyboard.HookProcedure? _hookProcedure;
    private nint _hook;
    private uint _threadId;
    private int _stopRequested;
    private int _closeMessagePosted;
    private int _closed;
    private int _hookRemovalSucceeded = 1;
    private string? _failureMessage;
    private KeyboardStartInfo? _startInfo;

    public PassThroughKeyboardSource(int eventCapacity = 256)
    {
        _mailbox = new BoundedKeyboardEventMailbox(eventCapacity);
    }

    /// <summary>The generation stamped on new events. Subscription updates increment it atomically.</summary>
    public long Generation => Volatile.Read(ref _subscriptions).Generation;

    /// <summary>Whether the native hook is currently installed and its message loop is active.</summary>
    public bool IsRunning => _hook != 0 && Volatile.Read(ref _stopRequested) == 0;

    /// <summary>Name of the dedicated native message thread, available after StartAsync begins.</summary>
    public string? HookThreadName
    {
        get
        {
            lock (_lifecycleLock)
                return _thread?.Name;
        }
    }

    /// <summary>First observed startup, callback, queue, or shutdown failure, if any.</summary>
    public string? FailureMessage => Volatile.Read(ref _failureMessage);

    /// <summary>Starts the dedicated thread, installs the hook, and returns its one-time held-key seed.</summary>
    public Task<KeyboardStartInfo> StartAsync()
    {
        if (!OperatingSystem.IsWindows())
            return Task.FromException<KeyboardStartInfo>(new PlatformNotSupportedException("WH_KEYBOARD_LL is available only on Windows."));

        lock (_lifecycleLock)
        {
            if (Volatile.Read(ref _closed) != 0)
                throw new InvalidOperationException("A closed keyboard source cannot be started.");
            if (_thread is not null)
                throw new InvalidOperationException("A keyboard source instance can be started only once.");

            BumpGeneration();
            _thread = new Thread(RunNativeThread)
            {
                Name = "StarMap pass-through keyboard hook",
                IsBackground = true,
            };
            _thread.Start();
            return _startCompletion.Task;
        }
    }

    /// <summary>
    /// Atomically replaces the subscribed virtual-key set without restarting the hook.
    /// Modifier sides are always published. All VK state remains tracked so WasDown is meaningful
    /// when a newly subscribed key is already held and emits an auto-repeat.
    /// </summary>
    public long UpdateSubscriptions(IEnumerable<int> virtualKeys)
    {
        ArgumentNullException.ThrowIfNull(virtualKeys);
        var keys = new HashSet<int>();
        foreach (var key in virtualKeys)
        {
            if (key is < 1 or > 255)
                throw new ArgumentOutOfRangeException(nameof(virtualKeys), key, "Virtual keys must be between 1 and 255.");
            keys.Add(key);
        }

        while (true)
        {
            var current = Volatile.Read(ref _subscriptions);
            var next = new SubscriptionSnapshot(checked(current.Generation + 1), keys);
            if (ReferenceEquals(Interlocked.CompareExchange(ref _subscriptions, next, current), current))
                return next.Generation;
        }
    }

    /// <summary>Reads events off the hook callback and off the native message thread.</summary>
    public IAsyncEnumerable<KeyboardEdge> ReadAllAsync(CancellationToken cancellationToken = default) =>
        _mailbox.ReadAllAsync(cancellationToken);

    /// <summary>
    /// Requests WM_QUIT on the hook's own message thread and waits at most the supplied duration.
    /// A failed post or timeout is returned and retained in FailureMessage.
    /// </summary>
    public async Task<KeyboardCloseResult> CloseAsync(TimeSpan? timeout = null)
    {
        var budget = timeout ?? DefaultCloseTimeout;
        if (budget <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(timeout));

        Thread? thread;
        lock (_lifecycleLock)
        {
            thread = _thread;
            if (thread is null)
            {
                // StartAsync uses the same lock, so it cannot start after this mailbox is completed.
                Volatile.Write(ref _closed, 1);
                _mailbox.Complete(FailureMessage is null ? null : new InvalidOperationException(FailureMessage));
                return new KeyboardCloseResult(true, FailureMessage);
            }
        }
        Interlocked.Exchange(ref _stopRequested, 1);
        var deadline = Stopwatch.GetTimestamp() + (long)(budget.TotalSeconds * Stopwatch.Frequency);
        var queueReady = await WaitWithinAsync(_queueReady.Task, deadline).ConfigureAwait(false);
        if (!queueReady)
        {
            SetFailure("Keyboard hook thread did not create its message queue before the close timeout.");
        }
        if (queueReady && !_threadStopped.Task.IsCompleted &&
            Interlocked.CompareExchange(ref _closeMessagePosted, 2, 0) == 0)
        {
            var threadId = Volatile.Read(ref _threadId);
            if (threadId == 0 || !NativeMethodsForKeyboard.PostThreadMessage(threadId, WmQuit, 0, 0))
            {
                SetFailure($"PostThreadMessage(WM_QUIT) failed (Win32 error {Marshal.GetLastWin32Error()}).");
                Volatile.Write(ref _closeMessagePosted, 0);
            }
            else
            {
                Volatile.Write(ref _closeMessagePosted, 1);
            }
        }

        if (!await WaitWithinAsync(_threadStopped.Task, deadline).ConfigureAwait(false))
            SetFailure("Keyboard hook thread did not stop before the close timeout.");

        // The completion signal is emitted in the thread's finally block just before return.
        // Join the actual owner thread inside the original deadline before reporting success.
        var joinTask = Task.Run(() =>
        {
            var remainingTicks = Math.Max(0, deadline - Stopwatch.GetTimestamp());
            var remaining = TimeSpan.FromSeconds((double)remainingTicks / Stopwatch.Frequency);
            return thread.Join(remaining);
        });
        var joinCompleted = await WaitWithinAsync(joinTask, deadline).ConfigureAwait(false);
        var joined = joinCompleted && await joinTask.ConfigureAwait(false);
        if (!joined)
            SetFailure("Keyboard hook thread did not exit before the close timeout.");

        var stopped = joined && Volatile.Read(ref _hookRemovalSucceeded) != 0;
        if (stopped)
            Interlocked.Exchange(ref _closed, 1);
        else if (_threadStopped.Task.IsCompleted)
            Interlocked.Exchange(ref _closed, 1);
        return new KeyboardCloseResult(stopped, FailureMessage);
    }

    public async ValueTask DisposeAsync()
    {
        var result = await CloseAsync().ConfigureAwait(false);
        if (!result.Stopped)
            throw new TimeoutException(result.FailureMessage ?? "Keyboard hook thread did not stop.");
    }

    private void RunNativeThread()
    {
        var state = new KeyboardEventState();
        try
        {
            NativeMethodsForKeyboard.PeekMessage(out _, 0, 0, 0, PmNoRemove);
            Volatile.Write(ref _threadId, NativeMethodsForKeyboard.GetCurrentThreadId());
            _queueReady.TrySetResult();
            if (Volatile.Read(ref _stopRequested) != 0)
            {
                _startCompletion.TrySetCanceled();
                return;
            }

            var initialSubscriptions = Volatile.Read(ref _subscriptions);
            state.Seed(NativeMethodsForKeyboard.GetAsyncKeyState);
            var initiallyHeld = state.GetHeldKeys(initialSubscriptions.Keys);
            if (Volatile.Read(ref _stopRequested) != 0)
            {
                _startCompletion.TrySetCanceled();
                return;
            }

            _hookProcedure = HookCallback;
            _stateForCallback = state;
            _hook = NativeMethodsForKeyboard.SetWindowsHookEx(WhKeyboardLl, _hookProcedure,
                NativeMethodsForKeyboard.GetModuleHandle(null), 0);
            if (_hook == 0)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetWindowsHookEx(WH_KEYBOARD_LL) failed.");

            _startInfo = new KeyboardStartInfo(initialSubscriptions.Generation, Array.AsReadOnly(initiallyHeld));
            _startCompletion.TrySetResult(_startInfo);
            while (true)
            {
                var messageResult = NativeMethodsForKeyboard.GetMessage(out _, 0, 0, 0);
                if (messageResult == -1)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "GetMessage failed on the keyboard hook thread.");
                if (messageResult == 0)
                    break;
                if (Volatile.Read(ref _stopRequested) != 0)
                    break;
            }
        }
        catch (Exception ex)
        {
            SetFailure($"{ex.GetType().Name}: {ex.Message}");
            _startCompletion.TrySetException(ex);
        }
        finally
        {
            if (_hook != 0)
            {
                try
                {
                    if (!NativeMethodsForKeyboard.UnhookWindowsHookEx(_hook))
                    {
                        Volatile.Write(ref _hookRemovalSucceeded, 0);
                        SetFailure($"UnhookWindowsHookEx failed (Win32 error {Marshal.GetLastWin32Error()}).");
                    }
                }
                catch (Exception ex)
                {
                    Volatile.Write(ref _hookRemovalSucceeded, 0);
                    SetFailure($"UnhookWindowsHookEx failed: {ex.GetType().Name}: {ex.Message}");
                }
                finally
                {
                    _hook = 0;
                }
            }
            _hookProcedure = null;
            _stateForCallback = null;
            if (_startInfo is null)
            {
                var startupError = new InvalidOperationException(FailureMessage ?? "Keyboard hook startup failed.");
                _startCompletion.TrySetException(startupError);
            }
            _queueReady.TrySetResult();
            _mailbox.Complete(FailureMessage is null ? null : new InvalidOperationException(FailureMessage));
            Volatile.Write(ref _threadId, 0);
            _threadStopped.TrySetResult();
        }
    }

    private nint HookCallback(int code, nuint wParam, nint lParam)
    {
        nint next;
        try
        {
            if (code >= 0 && Volatile.Read(ref _stopRequested) == 0 &&
                wParam is WmKeyDown or WmKeyUp or WmSysKeyDown or WmSysKeyUp)
            {
                var native = Marshal.PtrToStructure<LowLevelKeyboardInput>(lParam);
                var isDown = wParam is WmKeyDown or WmSysKeyDown;
                isDown = (native.Flags & LlkhfUp) == 0 && isDown;
                var snapshot = Volatile.Read(ref _subscriptions);
                var edge = _stateForCallback!.Observe(
                    checked((int)native.VirtualKey), checked((int)native.ScanCode), native.Flags,
                    isDown, Stopwatch.GetTimestamp(), NativeMethodsForKeyboard.GetForegroundWindow(), snapshot.Generation);
                if (snapshot.Keys.Contains(edge.PhysicalVirtualKey) || KeyboardEventState.IsModifier(edge.PhysicalVirtualKey))
                {
                    if (!_mailbox.TryWrite(edge, RequestCallbackFailure))
                        Interlocked.Exchange(ref _stopRequested, 1);
                }
            }
        }
        catch
        {
            RequestCallbackFailure("Keyboard hook callback failed while copying a native edge.");
        }
        finally
        {
            // Every path through the hook callback remains pass-through.
            try
            {
                next = NativeMethodsForKeyboard.CallNextHookEx(_hook, code, wParam, lParam);
            }
            catch
            {
                RequestCallbackFailure("CallNextHookEx failed from the pass-through keyboard callback.");
                next = 0;
            }
        }

        return next;
    }

    // Assigned on the hook thread before installation and used only by the hook callback.
    private KeyboardEventState? _stateForCallback;

    private void RequestCallbackFailure() => RequestCallbackFailure("Keyboard event channel overflowed; the source has stopped.");

    private void RequestCallbackFailure(string message)
    {
        SetFailure(message);
        Interlocked.Exchange(ref _stopRequested, 1);
        var threadId = Volatile.Read(ref _threadId);
        if (threadId != 0)
            NativeMethodsForKeyboard.PostThreadMessage(threadId, WmQuit, 0, 0);
    }

    private void SetFailure(string message)
    {
        Interlocked.CompareExchange(ref _failureMessage, message, null);
    }

    private void BumpGeneration()
    {
        while (true)
        {
            var current = Volatile.Read(ref _subscriptions);
            var next = new SubscriptionSnapshot(checked(current.Generation + 1), current.Keys);
            if (ReferenceEquals(Interlocked.CompareExchange(ref _subscriptions, next, current), current))
                return;
        }
    }

    private static async Task<bool> WaitWithinAsync(Task task, long deadline)
    {
        var remainingTicks = deadline - Stopwatch.GetTimestamp();
        if (remainingTicks <= 0)
            return task.IsCompleted;
        var delay = TimeSpan.FromSeconds((double)remainingTicks / Stopwatch.Frequency);
        return await Task.WhenAny(task, Task.Delay(delay)).ConfigureAwait(false) == task;
    }

    private sealed record SubscriptionSnapshot(long Generation, HashSet<int> Keys);

    [StructLayout(LayoutKind.Sequential)]
    private struct LowLevelKeyboardInput
    {
        public uint VirtualKey;
        public uint ScanCode;
        public uint Flags;
        public uint Time;
        public nuint ExtraInfo;
    }

    private static class NativeMethodsForKeyboard
    {
        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        internal delegate nint HookProcedure(int code, nuint wParam, nint lParam);

        [StructLayout(LayoutKind.Sequential)]
        internal struct Message
        {
            internal nint Hwnd;
            internal uint Id;
            internal nuint WParam;
            internal nint LParam;
            internal uint Time;
            internal int X;
            internal int Y;
            internal uint Private;
        }

        [DllImport("user32.dll", EntryPoint = "SetWindowsHookExW", SetLastError = true)]
        internal static extern nint SetWindowsHookEx(int hookId, HookProcedure procedure, nint module, uint threadId);

        [DllImport("user32.dll", EntryPoint = "CallNextHookEx", SetLastError = true)]
        internal static extern nint CallNextHookEx(nint hook, int code, nuint wParam, nint lParam);

        [DllImport("user32.dll", EntryPoint = "UnhookWindowsHookEx", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool UnhookWindowsHookEx(nint hook);

        [DllImport("user32.dll", EntryPoint = "GetAsyncKeyState", SetLastError = true)]
        internal static extern short GetAsyncKeyState(int virtualKey);

        [DllImport("user32.dll", EntryPoint = "GetForegroundWindow")]
        internal static extern nint GetForegroundWindow();

        [DllImport("user32.dll", EntryPoint = "PeekMessageW", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool PeekMessage(out Message message, nint hwnd, uint min, uint max, uint remove);

        [DllImport("user32.dll", EntryPoint = "GetMessageW", SetLastError = true)]
        internal static extern int GetMessage(out Message message, nint hwnd, uint min, uint max);

        [DllImport("user32.dll", EntryPoint = "PostThreadMessageW", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool PostThreadMessage(uint threadId, uint message, nuint wParam, nint lParam);

        [DllImport("kernel32.dll", EntryPoint = "GetCurrentThreadId")]
        internal static extern uint GetCurrentThreadId();

        [DllImport("kernel32.dll", EntryPoint = "GetModuleHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern nint GetModuleHandle(string? moduleName);
    }

}

/// <summary>Per-hook-thread physical key levels and deterministic conversion of raw hook facts.</summary>
internal sealed class KeyboardEventState
{
    private readonly bool[] _held = new bool[256];

    internal void Seed(Func<int, short> getAsyncKeyState)
    {
        ArgumentNullException.ThrowIfNull(getAsyncKeyState);
        for (var key = 1; key <= 255; key++)
        {
            if (key is 0x10 or 0x11 or 0x12)
                continue;
            _held[key] = (getAsyncKeyState(key) & unchecked((short)0x8000)) != 0;
        }
    }

    internal int[] GetHeldKeys(IReadOnlySet<int> subscriptions)
    {
        var relevant = new List<int>();
        for (var key = 1; key <= 255; key++)
        {
            if (_held[key] && (subscriptions.Contains(key) || IsModifier(key)))
                relevant.Add(key);
        }
        return relevant.ToArray();
    }

    internal KeyboardEdge Observe(
        int virtualKey,
        int scanCode,
        uint flags,
        bool isDown,
        long timestamp,
        nint foregroundHwnd,
        long generation)
    {
        if (virtualKey is < 0 or > 255)
            throw new ArgumentOutOfRangeException(nameof(virtualKey));
        var physical = ToPhysicalVirtualKey(virtualKey, scanCode, flags);
        var wasDown = _held[physical];
        _held[physical] = isDown;
        return new KeyboardEdge(virtualKey, physical, isDown, wasDown,
            timestamp, foregroundHwnd, generation);
    }

    internal static int ToPhysicalVirtualKey(int virtualKey, int scanCode, uint flags) => virtualKey switch
    {
        0x10 => scanCode == 0x36 ? 0xA1 : 0xA0, // Shift: right side is identified by scan code.
        0x11 => (flags & 0x01) != 0 ? 0xA3 : 0xA2, // Ctrl: extended is right.
        0x12 => (flags & 0x01) != 0 ? 0xA5 : 0xA4, // Alt: extended is right.
        _ => virtualKey,
    };

    internal static bool IsModifier(int virtualKey) => virtualKey is
        0x5B or 0x5C or 0xA0 or 0xA1 or 0xA2 or 0xA3 or 0xA4 or 0xA5;
}

internal sealed class BoundedKeyboardEventMailbox
{
    private readonly Channel<KeyboardEdge> _channel;
    private int _overflowed;

    internal BoundedKeyboardEventMailbox(int capacity)
    {
        if (capacity < 1)
            throw new ArgumentOutOfRangeException(nameof(capacity));
        _channel = Channel.CreateBounded<KeyboardEdge>(new BoundedChannelOptions(capacity)
        {
            AllowSynchronousContinuations = false,
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = true,
        });
    }

    internal bool TryWrite(KeyboardEdge item, Action overflow)
    {
        if (Volatile.Read(ref _overflowed) != 0)
            return false;
        if (_channel.Writer.TryWrite(item))
            return true;
        if (Interlocked.Exchange(ref _overflowed, 1) == 0)
            overflow();
        return false;
    }

    internal IAsyncEnumerable<KeyboardEdge> ReadAllAsync(CancellationToken cancellationToken) =>
        _channel.Reader.ReadAllAsync(cancellationToken);

    internal void Complete(Exception? error) => _channel.Writer.TryComplete(error);
}
