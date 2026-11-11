using System.Buffers.Binary;
using System.ComponentModel;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text.Json;
using StarMap.Bridge;
using StarMap.Windows;

namespace StarMap.Desktop;

/// <summary>Independent callback lane; never enters the business RPC or UI task queue.</summary>
public sealed class NativeCaptureService : IWorkerCaptureService
{
    private readonly NamedPipeServerStream pipe;
    private readonly CancellationTokenSource closing = new();
    private readonly TaskCompletionSource<int> worker = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly object gate = new();
    private readonly Task server;
    private readonly Func<WindowSnapshot, bool> targetPolicy;
    private readonly Func<WindowSnapshot, ClientRect, CancellationToken, Task<CapturedClientFrame>> capture;
    private readonly Func<WindowSnapshot, CancellationToken, Task<CapturedClientFrame>> fullCapture;
    private readonly Func<nint, WindowSnapshot> inspect;
    private Scope? current;
    private WindowIdentity? target;
    private long targetRevision, lastCall;
    private Task? disposal;
    private readonly HashSet<Task> pixelTasks = [];
    public string PipeName { get; } = "starmap-native-" + Guid.NewGuid().ToString("N");
    public Exception? Failure { get; private set; }
    public long PixelCalls { get; private set; }

    public NativeCaptureService(Func<WindowSnapshot, CancellationToken, Task<CapturedClientFrame>> fullCapture,
        Func<nint, WindowSnapshot>? inspect = null,
        Func<WindowSnapshot, ClientRect, CancellationToken, Task<CapturedClientFrame>>? capture = null,
        Func<WindowSnapshot, bool>? targetPolicy = null)
    {
        this.inspect = inspect ?? WindowCapture.Inspect;
        this.capture = capture ?? ((snapshot, roi, token) => Task.Run(() => WindowCapture.CaptureRegion(snapshot, roi, token), token));
        this.fullCapture = fullCapture;
        this.targetPolicy = targetPolicy ?? WindowCapture.IsIdentityV;
        pipe = new(PipeName, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly, 65536, 65536);
        server = ServeAsync();
    }

    public WindowSnapshot SelectTarget(nint hwnd)
    {
        var snapshot = inspect(hwnd);
        if (!targetPolicy(snapshot)) throw new InvalidOperationException("所选窗口不符合游戏标题和进程身份。");
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(closing.IsCancellationRequested, this);
            target = snapshot.Identity; targetRevision++;
        }
        return snapshot;
    }

    public WindowSnapshot? CurrentTarget()
    {
        WindowIdentity? selected;
        lock (gate) selected = target;
        if (selected is null) return null;
        var actual = inspect(selected.Hwnd);
        return SameIdentity(selected, actual.Identity) && targetPolicy(actual) ? actual : null;
    }

    public bool IsRenderCurrent(JsonElement render)
    {
        try
        {
            if (Failure is not null || closing.IsCancellationRequested) return false;
            var value = CurrentTarget();
            return value is not null && value.ForegroundHwnd == value.Identity.Hwnd && !value.Minimized
                && render.GetProperty("liveAuthorization").GetBoolean()
                && render.GetProperty("evidenceKind").GetString() == "live-client-capture"
                && render.GetProperty("clientWidth").GetInt32() == value.Geometry.Width
                && render.GetProperty("clientHeight").GetInt32() == value.Geometry.Height
                && render.GetProperty("originX").GetInt32() == value.Geometry.Left
                && render.GetProperty("originY").GetInt32() == value.Geometry.Top
                && render.GetProperty("captureDpi").GetUInt32() == value.Geometry.Dpi;
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception or KeyNotFoundException or FormatException) { return false; }
    }

    public void BindWorker(int processId)
    {
        if (processId <= 0 || !worker.TrySetResult(processId)) throw new InvalidOperationException("Native callback worker already bound.");
    }

    public IDisposable BeginRequest(string session, long requestId, string operation, CancellationToken cancellation)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(closing.IsCancellationRequested, this);
            if (Failure is not null) throw new IOException("本机捕获通道已断开，请重新连接。", Failure);
            if (current is not null) throw new InvalidOperationException("A native request is still active.");
            var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellation, closing.Token);
            var scope = new Scope(session, requestId, operation == "dispatch", target, targetRevision, linked.Token);
            current = scope;
            return new Release(() => { lock (gate) { if (ReferenceEquals(current, scope)) current = null; } linked.Dispose(); });
        }
    }

    private async Task ServeAsync()
    {
        try
        {
            await pipe.WaitForConnectionAsync(closing.Token).ConfigureAwait(false);
            var expectedPid = await worker.Task.WaitAsync(closing.Token).ConfigureAwait(false);
            if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out var actualPid) || actualPid != expectedPid)
                throw new IOException("Native callback did not originate from its owned worker.");
            var prefix = new byte[4];
            while (!closing.IsCancellationRequested)
            {
                if (await pipe.ReadAsync(prefix.AsMemory(0, 1), closing.Token).ConfigureAwait(false) == 0)
                {
                    if (!closing.IsCancellationRequested) { lock (gate) Failure = new EndOfStreamException("Native callback channel closed."); }
                    break;
                }
                using var readDeadline = CancellationTokenSource.CreateLinkedTokenSource(closing.Token);
                readDeadline.CancelAfter(TimeSpan.FromSeconds(2));
                await pipe.ReadExactlyAsync(prefix.AsMemory(1), readDeadline.Token).ConfigureAwait(false);
                var length = BinaryPrimitives.ReadUInt32LittleEndian(prefix);
                if (length is 0 or > 65536) throw new InvalidDataException("Native callback header exceeds its limit.");
                var header = new byte[length];
                await pipe.ReadExactlyAsync(header, readDeadline.Token).ConfigureAwait(false);
                using var document = JsonDocument.Parse(header);
                var request = document.RootElement;
                var session = request.GetProperty("session").GetString()!;
                var requestId = request.GetProperty("requestId").GetInt64();
                var call = request.GetProperty("callId").GetInt64();
                if (call <= lastCall) throw new InvalidDataException("Native callback call ID was reused.");
                lastCall = call;
                object? result = null; object? error = null; byte[] pixels = [];
                try
                {
                    Scope scope;
                    lock (gate) scope = current is { } active && active.Session == session && active.RequestId == requestId
                        ? active : throw new NativeCallbackException("stale", "Native callback does not belong to the active request.");
                    scope.Token.ThrowIfCancellationRequested();
                    using var acquisition = CancellationTokenSource.CreateLinkedTokenSource(scope.Token, closing.Token);
                    acquisition.CancelAfter(TimeSpan.FromSeconds(2));
                    var captureToken = acquisition.Token;
                    var operation = request.GetProperty("operation").GetString();
                    if (operation == "is_window")
                    {
                        try { ReadTarget(scope, request); result = true; }
                        catch (NativeCallbackException) { result = false; }
                        catch (Win32Exception) { result = false; }
                        catch (InvalidOperationException) { result = false; }
                    }
                    else
                    {
                        var snapshot = ReadTarget(scope, request, operation == "foreground_window");
                        if (operation == "foreground_window") result = snapshot.ForegroundHwnd.ToInt64();
                        else if (operation == "context")
                        {
                            if (snapshot.Minimized) throw new NativeCallbackException("native-error", "Bound window is minimized.");
                            result = Context(snapshot.Geometry);
                        }
                        else if (operation is "capture" or "full_capture")
                        {
                            if (!scope.AllowPixels) throw new NativeCallbackException("capture-not-authorized", "This business operation cannot acquire pixels.");
                            if (snapshot.ForegroundHwnd != snapshot.Identity.Hwnd)
                                throw new NativeCallbackException("foreground-changed", "Bound game must own the foreground.");
                            PixelCalls++;
                            CapturedClientFrame frame;
                            if (operation == "full_capture") frame = await Track(fullCapture(snapshot, captureToken)).WaitAsync(captureToken).ConfigureAwait(false);
                            else
                            {
                                var values = request.GetProperty("roi").EnumerateArray().Select(value => value.GetInt32()).ToArray();
                                if (values.Length != 4) throw new InvalidDataException("Capture ROI must have four coordinates.");
                                frame = await Track(capture(snapshot, new(values[0], values[1], values[2], values[3]), captureToken)).WaitAsync(captureToken).ConfigureAwait(false);
                            }
                            scope.Token.ThrowIfCancellationRequested();
                            var after = ReadTarget(scope, request);
                            if (!SameIdentity(after.Identity, frame.Context.Identity) || after.Geometry != frame.Context.Geometry || after.ForegroundHwnd != after.Identity.Hwnd)
                                throw new NativeCallbackException("capture-context-changed", "Window geometry or foreground changed during capture.");
                            pixels = Bgr(frame);
                            result = new { width = frame.Width, height = frame.Height, context = Context(frame.Context.Geometry) };
                        }
                        else throw new NativeCallbackException("unknown-operation", "Unknown native capture operation.");
                    }
                }
                catch (OperationCanceledException) { error = new { code = "cancelled", message = "Native capture request cancelled." }; }
                catch (NativeCallbackException failure) { error = new { code = failure.Code, message = failure.Message, captureCapability = failure.Capability }; }
                catch (Exception failure) { error = new { code = "native-error", message = failure.Message }; }
                // A frame is atomic on this channel: after its header, cancellation
                // may only finish that frame or close the pipe, never append an error.
                using var writeDeadline = CancellationTokenSource.CreateLinkedTokenSource(closing.Token);
                writeDeadline.CancelAfter(TimeSpan.FromSeconds(2));
                if (error is not null) pixels = [];
                var reply = JsonSerializer.SerializeToUtf8Bytes(new { session, requestId, callId = call, ok = error is null, result, error, payloadLength = pixels.Length });
                BinaryPrimitives.WriteUInt32LittleEndian(prefix, checked((uint)reply.Length));
                await pipe.WriteAsync(prefix, writeDeadline.Token).ConfigureAwait(false);
                await pipe.WriteAsync(reply, writeDeadline.Token).ConfigureAwait(false);
                if (pixels.Length > 0) await pipe.WriteAsync(pixels, writeDeadline.Token).ConfigureAwait(false);
                await pipe.FlushAsync(writeDeadline.Token).ConfigureAwait(false);
            }
        }
        catch (Exception error)
        {
            if (!closing.IsCancellationRequested) { lock (gate) Failure = error; }
        }
        finally { pipe.Dispose(); }
    }

    private WindowSnapshot ReadTarget(Scope scope, JsonElement request, bool noHwnd = false)
    {
        scope.Token.ThrowIfCancellationRequested();
        lock (gate)
            if (!ReferenceEquals(scope, current) || scope.Revision != targetRevision || target is null || scope.Target is null)
                throw new NativeCallbackException("binding-unavailable", "Native target changed or has not been selected.");
        if (!noHwnd && request.GetProperty("hwnd").GetInt64() != scope.Target.Hwnd.ToInt64())
            throw new NativeCallbackException("binding-unavailable", "Native request does not target the selected window.");
        var snapshot = inspect(scope.Target.Hwnd);
        if (!SameIdentity(scope.Target, snapshot.Identity) || !targetPolicy(snapshot))
            throw new NativeCallbackException("binding-unavailable", "Bound process or window identity changed.");
        return snapshot;
    }

    public static object Context(ClientGeometry value) => new { fullClientSize = new[] { value.Width, value.Height },
        clientScreenBounds = new[] { value.Left, value.Top, value.Right, value.Bottom }, dpi = value.Dpi };

    private static bool SameIdentity(WindowIdentity a, WindowIdentity b) => a.Hwnd == b.Hwnd && a.ProcessId == b.ProcessId
        && a.ProcessCreationUtcTicks == b.ProcessCreationUtcTicks && string.Equals(a.ExecutablePath, b.ExecutablePath, StringComparison.OrdinalIgnoreCase);

    private static byte[] Bgr(CapturedClientFrame frame)
    {
        if (frame.Width <= 0 || frame.Height <= 0 || (long)frame.Width * frame.Height > 32_000_000
            || frame.Stride != checked(frame.Width * 4) || frame.Bgra32.Length != checked(frame.Stride * frame.Height))
            throw new InvalidDataException("Native capture returned invalid pixel dimensions.");
        var source = frame.Bgra32.Span;
        var result = new byte[checked(frame.Width * frame.Height * 3)];
        for (int s = 0, d = 0; d < result.Length; s += 4, d += 3)
        { result[d] = source[s]; result[d + 1] = source[s + 1]; result[d + 2] = source[s + 2]; }
        return result;
    }

    public void CancelPending() => closing.Cancel();
    private Task<CapturedClientFrame> Track(Task<CapturedClientFrame> task)
    {
        lock (gate) pixelTasks.Add(task);
        _ = task.ContinueWith(done =>
        {
            _ = done.Exception;
            lock (gate) pixelTasks.Remove(done);
        }, CancellationToken.None, TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
        return task;
    }
    public ValueTask DisposeAsync()
    {
        lock (gate) return new(disposal ??= CloseAsync());
    }
    private async Task CloseAsync()
    {
        closing.Cancel(); pipe.Dispose();
        await server.WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
        Task[] pending;
        lock (gate) pending = pixelTasks.ToArray();
        if (pending.Length > 0) await Task.WhenAll(pending).WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
    }
    private sealed record Scope(string Session, long RequestId, bool AllowPixels, WindowIdentity? Target, long Revision, CancellationToken Token);
    private sealed class Release(Action release) : IDisposable { public void Dispose() => release(); }
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientProcessId(nint pipe, out uint clientProcessId);
}

public sealed class NativeCallbackException(string code, string message, object? capability = null) : Exception(message)
{
    public string Code { get; } = code;
    public object? Capability { get; } = capability;
}
