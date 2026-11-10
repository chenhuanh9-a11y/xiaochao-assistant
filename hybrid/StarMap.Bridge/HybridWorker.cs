using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;

namespace StarMap.Bridge;

/// <summary>Serialized JSONL transport for the persistent browse/runtime Python service.</summary>
public sealed class HybridWorker : IWorkerConnection
{
    private const int MaxRequestBytes = 64 * 1024;
    private const int MaxResponseChars = 18 * 1024 * 1024;
    private const int MaxDiagnosticChars = 16 * 1024;
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    private static readonly TimeSpan ShutdownTimeout = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan ReadyTimeout = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan CancelWriteTimeout = TimeSpan.FromSeconds(1);
    private static readonly string[] RuntimeOperations = ["prepare", "set_frame", "dispatch", "snapshot"];

    private readonly Process process;
    private readonly int processId;
    private readonly string session = Guid.NewGuid().ToString("D");
    private readonly TimeSpan requestTimeout;
    private readonly SemaphoreSlim serial = new(1, 1);
    private readonly SemaphoreSlim inputWriter = new(1, 1);
    private readonly CancellationTokenSource stopWriting = new();
    private readonly Channel<string> messages = Channel.CreateBounded<string>(new BoundedChannelOptions(2)
    {
        FullMode = BoundedChannelFullMode.Wait,
        SingleReader = true,
        SingleWriter = true,
        AllowSynchronousContinuations = false,
    });
    private readonly Task stdoutPump;
    private readonly Task stderrPump;
    private readonly object diagnosticGate = new();
    private readonly StringBuilder diagnostics = new();
    private readonly object closeGate = new();
    private Task? closing;
    private Task? readyTask;
    private long nextId;
    private volatile bool stopping;
    private volatile bool exited;
    private volatile bool supportsRuntime;
    private volatile bool discardOutput;
    private volatile bool transportFailed;
    private volatile bool forcedTermination;
    private bool ready;
    private Exception? terminalError;

    private HybridWorker(Process process, TimeSpan timeout)
    {
        this.process = process;
        processId = process.Id;
        requestTimeout = NormalizeTimeout(timeout);
        stdoutPump = PumpStdoutAsync();
        stderrPump = DrainErrorsAsync();
    }

    public int ProcessId => processId;
    public bool HasExited
    {
        get
        {
            if (exited) return true;
            try { return process.HasExited; }
            catch (ObjectDisposedException) { return true; }
            catch (InvalidOperationException) { return true; }
        }
    }
    public bool SupportsRuntime => supportsRuntime;
    public string DiagnosticTail { get { lock (diagnosticGate) return diagnostics.ToString(); } }

    public static HybridWorker Start(string python, string workerScript, string resourceRoot,
        string dataRoot, string frameRoot, string? installationRoot = null,
        TimeSpan? requestTimeout = null)
    {
        if (!Path.IsPathFullyQualified(workerScript) || !File.Exists(workerScript))
            throw new FileNotFoundException("找不到统一 Python 服务脚本，请选择当前源码根目录。", workerScript);
        RequireAbsoluteDirectory(resourceRoot, nameof(resourceRoot));
        RequireAbsoluteDirectory(frameRoot, nameof(frameRoot));
        if (!Path.IsPathFullyQualified(dataRoot))
            throw new ArgumentException("数据目录必须是绝对路径。", nameof(dataRoot));
        if (installationRoot is not null && !Path.IsPathFullyQualified(installationRoot))
            throw new ArgumentException("安装目录必须是绝对路径。", nameof(installationRoot));
        if (requestTimeout is { } supplied && supplied <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(requestTimeout), "请求超时必须大于零。 ");

        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = new UTF8Encoding(false, true),
            StandardErrorEncoding = new UTF8Encoding(false, true),
            WorkingDirectory = Path.GetDirectoryName(workerScript)!,
        };
        foreach (var arg in new[] { "-B", "-u", workerScript, "--resource-root", resourceRoot,
                     "--data-root", dataRoot, "--frame-root", frameRoot })
            start.ArgumentList.Add(arg);
        if (installationRoot is not null)
        {
            start.ArgumentList.Add("--installation-root");
            start.ArgumentList.Add(installationRoot);
        }
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        var process = Process.Start(start) ?? throw new IOException("无法启动统一 Python 服务。");
        try { return new HybridWorker(process, requestTimeout ?? TimeSpan.FromSeconds(30)); }
        catch
        {
            try { process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
            process.Dispose();
            throw;
        }
    }

    public async Task<WorkerHello> HelloAsync(CancellationToken cancellation = default)
    {
        var value = await InvokeAsync("hello", null, cancellation).ConfigureAwait(false);
        var hello = value.Deserialize<WorkerHello>(Json)
            ?? throw new InvalidDataException("Python 服务返回了空 hello。");
        supportsRuntime = hello.Operations is not null && RuntimeOperations.All(
            operation => hello.Operations.Contains(operation, StringComparer.Ordinal));
        return hello;
    }

    public async Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default)
    {
        var value = await InvokeAsync("catalog", new { season, mode }, cancellation).ConfigureAwait(false);
        return value.Deserialize<BrowseCatalog>(Json)
            ?? throw new InvalidDataException("Python 服务返回了空 catalog。");
    }

    public async Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default)
    {
        var value = await InvokeAsync("preview", new { catalogId, key }, cancellation).ConfigureAwait(false);
        return value.Deserialize<BrowsePreview>(Json)
            ?? throw new InvalidDataException("Python 服务返回了空 preview。");
    }

    public async Task<JsonElement> InvokeAsync(string operation, object? arguments = null,
        CancellationToken cancellation = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(operation);
        await serial.WaitAsync(cancellation).ConfigureAwait(false);
        try
        {
            cancellation.ThrowIfCancellationRequested();
            ThrowIfUnavailable();
            await EnsureReadyAsync(cancellation).ConfigureAwait(false);
            cancellation.ThrowIfCancellationRequested();

            var id = checked(++nextId);
            var request = MakeRequest(operation, arguments, id, GetRequestTimeoutMilliseconds());
            var payload = request.ToJsonString();
            if (Encoding.UTF8.GetByteCount(payload) > MaxRequestBytes)
                throw new InvalidDataException("Python 请求超过 64 KiB 协议限制。");

            using var timeout = new CancellationTokenSource(requestTimeout);
            var cancelSignal = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            using var registration = cancellation.Register(static state =>
                ((TaskCompletionSource)state!).TrySetResult(), cancelSignal);

            await WriteRequestAsync(payload, operation, cancellation, timeout.Token).ConfigureAwait(false);
            var response = await WaitForTerminalAsync(id, operation, cancellation, cancelSignal.Task, timeout.Token)
                .ConfigureAwait(false);
            if (cancellation.IsCancellationRequested) throw new OperationCanceledException(cancellation);
            if (!response.Ok)
                throw new WorkerException(response.ErrorCode ?? "worker-error", response.ErrorMessage ?? "Python 请求失败。");
            return response.Result;
        }
        finally { serial.Release(); }
    }

    private async Task<TerminalResponse> WaitForTerminalAsync(long id, string operation,
        CancellationToken callerCancellation, Task callerCancelled, CancellationToken timeout)
    {
        var timeoutObserved = false;
        var cancelSent = false;
        Task? drainDeadline = null;
        Task<JsonElement>? next = null;
        var timeoutTask = Task.Delay(Timeout.InfiniteTimeSpan, timeout);

        while (true)
        {
            if (next is { IsCompleted: true })
            {
                var message = await next.ConfigureAwait(false);
                next = null;
                TerminalResponse? terminal;
                try { terminal = MatchTerminal(message, id); }
                catch (InvalidDataException error)
                {
                    FailOutput(error);
                    throw new IOException("Python 服务返回了无效协议事件。" + DiagnosticTail, error);
                }
                if (terminal is not null)
                {
                    if (callerCancellation.IsCancellationRequested)
                        throw new OperationCanceledException(callerCancellation);
                    if (timeoutObserved || timeout.IsCancellationRequested)
                        throw new TimeoutException($"Python operation '{operation}' 超过 {requestTimeout.TotalMilliseconds:0} ms。");
                    return terminal;
                }
                continue;
            }

            if (!cancelSent && callerCancelled.IsCompleted)
            {
                drainDeadline ??= Task.Delay(ShutdownTimeout);
                cancelSent = true;
                await SendCancelAsync(id).ConfigureAwait(false);
            }
            if (!timeoutObserved && timeout.IsCancellationRequested)
            {
                timeoutObserved = true;
                drainDeadline ??= Task.Delay(ShutdownTimeout);
                if (!cancelSent)
                {
                    cancelSent = true;
                    await SendCancelAsync(id).ConfigureAwait(false);
                }
            }

            next ??= ReadMessageAsync();
            var waits = new List<Task>(4) { next };
            if (!cancelSent) waits.Add(callerCancelled);
            if (!timeoutObserved) waits.Add(timeoutTask);
            if (drainDeadline is not null) waits.Add(drainDeadline);
            var completed = await Task.WhenAny(waits).ConfigureAwait(false);
            if (completed == drainDeadline)
            {
                var error = new TimeoutException("Python 服务在取消后未排空当前请求终态。" + DiagnosticTail);
                SetTerminalError(error);
                await TerminateOwnedProcessAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false);
                throw new IOException("Python 服务取消清理超时，已终止本次自有进程。", error);
            }
            if (completed == next) continue;
            if (completed == callerCancelled)
            {
                if (!cancelSent)
                {
                    drainDeadline ??= Task.Delay(ShutdownTimeout);
                    cancelSent = true;
                    await SendCancelAsync(id).ConfigureAwait(false);
                }
                continue;
            }
            if (timeout.IsCancellationRequested) continue;
        }
    }

    private TerminalResponse? MatchTerminal(JsonElement message, long id)
    {
        if (!TryGetString(message, "event", out var eventName))
            throw new InvalidDataException("Python 服务事件缺少 event。");
        if (eventName != "result") return null; // progress and cancel-ack are bounded by the single active RPC.
        if (!TryGetString(message, "session", out var actualSession) || actualSession != session)
            return null;
        if (!message.TryGetProperty("id", out var actualId) || actualId.ValueKind != JsonValueKind.Number || !actualId.TryGetInt64(out var actual))
            throw new InvalidDataException("Python 终态的 id 必须是整数。");
        if (actual != id) return null; // A late terminal must never complete this request.

        if (!message.TryGetProperty("ok", out var okValue) || okValue.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException("Python 终态缺少布尔 ok 字段。");
        if (okValue.GetBoolean())
        {
            if (!message.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException("Python 成功终态缺少 result 对象。");
            return new TerminalResponse(true, result.Clone(), null, null);
        }

        string? code = null, text = null;
        if (message.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.Object)
        {
            if (TryGetString(error, "code", out var errorCode)) code = errorCode;
            if (TryGetString(error, "message", out var errorMessage)) text = errorMessage;
        }
        return new TerminalResponse(false, default, code, text);
    }

    private async Task EnsureReadyAsync(CancellationToken cancellation)
    {
        if (ready) return;
        readyTask ??= ValidateReadyAsync();
        await readyTask.WaitAsync(cancellation).ConfigureAwait(false);
    }

    private async Task ValidateReadyAsync()
    {
        JsonElement message;
        // The request deadline starts after ready; a short computation budget
        // must not turn Python process startup into that same short budget.
        try { message = await ReadMessageAsync().WaitAsync(ReadyTimeout).ConfigureAwait(false); }
        catch (TimeoutException timeout)
        {
            var error = new TimeoutException("等待 Python 服务 ready 超时。" + DiagnosticTail, timeout);
            FailOutput(error);
            throw new IOException(error.Message, error);
        }
        if (!message.TryGetProperty("ready", out var readyValue) || readyValue.ValueKind != JsonValueKind.True ||
            !TryGetString(message, "evidenceKind", out var evidence) || evidence != "recorded")
        {
            var error = new InvalidDataException("Python 服务没有返回 recorded ready 握手。" + DiagnosticTail);
            FailOutput(error);
            throw new IOException(error.Message, error);
        }
        ready = true;
    }

    private JsonObject MakeRequest(string operation, object? arguments, long id, int timeoutMilliseconds)
    {
        JsonObject request;
        if (arguments is null) request = new JsonObject();
        else
        {
            var node = JsonSerializer.SerializeToNode(arguments)
                ?? throw new ArgumentException("请求参数必须序列化为 JSON 对象。", nameof(arguments));
            request = node as JsonObject
                ?? throw new ArgumentException("请求参数必须是 JSON 对象。", nameof(arguments));
        }
        request["session"] = session;
        request["id"] = id;
        request["operation"] = operation;
        request["timeoutMs"] = timeoutMilliseconds;
        return request;
    }

    private async Task SendCancelAsync(long id)
    {
        if (stopping) return;
        var cancel = new JsonObject { ["operation"] = "cancel", ["session"] = session, ["id"] = id, ["timeoutMs"] = 1000 };
        using var deadline = new CancellationTokenSource(CancelWriteTimeout);
        using var write = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token, stopWriting.Token);
        var acquired = false;
        var writeStarted = false;
        try
        {
            await inputWriter.WaitAsync(write.Token).ConfigureAwait(false);
            acquired = true;
            if (stopping) return;
            write.Token.ThrowIfCancellationRequested();
            writeStarted = true;
            await process.StandardInput.WriteLineAsync(cancel.ToJsonString().AsMemory(), write.Token).ConfigureAwait(false);
            await process.StandardInput.FlushAsync(write.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException error)
        {
            if (writeStarted)
            {
                var writeFailure = new IOException("取消帧写入被中断；连接已终止，当前 RPC 不会重试。", error);
                await FailAndTerminateAsync(writeFailure).ConfigureAwait(false);
                throw writeFailure;
            }
            if (stopping || stopWriting.IsCancellationRequested) return;
            if (deadline.IsCancellationRequested) return; // No bytes were written; keep draining the original terminal.
            throw;
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException)
        {
            if (writeStarted)
            {
                var writeFailure = new IOException("取消帧写入失败；连接已终止，当前 RPC 不会重试。", error);
                await FailAndTerminateAsync(writeFailure).ConfigureAwait(false);
                throw writeFailure;
            }
            if (stopping) return;
            var cancelFailure = new IOException("写入 Python 服务取消帧失败；连接已终止。" + DiagnosticTail, error);
            await FailAndTerminateAsync(cancelFailure).ConfigureAwait(false);
            throw cancelFailure;
        }
        finally { if (acquired) inputWriter.Release(); }
    }

    private async Task WriteRequestAsync(string payload, string operation,
        CancellationToken callerCancellation, CancellationToken requestDeadline)
    {
        using var write = new CancellationTokenSource();
        var firstCancellation = 0;
        void CancelWrite(int reason)
        {
            // Pipe cancellation can finish after another token fires. Record the
            // original trigger before cancelling I/O, rather than re-reading tokens.
            if (Interlocked.CompareExchange(ref firstCancellation, reason, 0) == 0) write.Cancel();
        }
        using var callerRegistration = callerCancellation.Register(() => CancelWrite(1));
        using var deadlineRegistration = requestDeadline.Register(() => CancelWrite(2));
        using var stopRegistration = stopWriting.Token.Register(() => CancelWrite(3));
        var acquired = false;
        var writeStarted = false;
        try
        {
            await inputWriter.WaitAsync(write.Token).ConfigureAwait(false);
            acquired = true;
            if (stopping) throw new ObjectDisposedException(nameof(HybridWorker));
            write.Token.ThrowIfCancellationRequested();
            writeStarted = true;
            await process.StandardInput.WriteLineAsync(payload.AsMemory(), write.Token).ConfigureAwait(false);
            await process.StandardInput.FlushAsync(write.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException error)
        {
            if (!writeStarted)
            {
                if (stopping || stopWriting.IsCancellationRequested)
                    throw new ObjectDisposedException(nameof(HybridWorker), error.Message);
                if (callerCancellation.IsCancellationRequested)
                    throw new OperationCanceledException(callerCancellation);
                if (requestDeadline.IsCancellationRequested)
                    throw new TimeoutException($"Python operation '{operation}' 超时，未提交请求。", error);
                throw;
            }

            var kind = firstCancellation switch
            {
                1 => WorkerWriteFailureKind.CallerCancelled,
                3 => WorkerWriteFailureKind.ConnectionClosing,
                _ => WorkerWriteFailureKind.RequestDeadline,
            };
            var failure = new WorkerWriteInterruptedException(
                kind,
                $"Python operation '{operation}' 的 JSONL 写入在发送后被中断；连接已终止且不会重试。", error);
            await FailAndTerminateAsync(failure).ConfigureAwait(false);
            throw failure;
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException)
        {
            if (writeStarted)
            {
                var failure = new IOException($"Python operation '{operation}' 的 JSONL 写入失败；连接已终止且不会重试。", error);
                await FailAndTerminateAsync(failure).ConfigureAwait(false);
                throw failure;
            }
            if (stopping) throw new ObjectDisposedException(nameof(HybridWorker), error.Message);
            await FailAndTerminateAsync(error).ConfigureAwait(false);
            throw new IOException("写入 Python 服务失败。" + DiagnosticTail, error);
        }
        finally { if (acquired) inputWriter.Release(); }
    }

    private async Task FailAndTerminateAsync(Exception error)
    {
        FailOutput(error);
        await TerminateOwnedProcessAsync().ConfigureAwait(false);
    }

    private int GetRequestTimeoutMilliseconds() => Math.Clamp((int)Math.Ceiling(requestTimeout.TotalMilliseconds), 1, 60_000);

    private void ThrowIfUnavailable()
    {
        if (stopping) throw new ObjectDisposedException(nameof(HybridWorker));
        if (terminalError is not null) throw new IOException("Python 服务已断开，请重新连接。" + DiagnosticTail, terminalError);
    }

    private async Task<JsonElement> ReadMessageAsync()
    {
        try
        {
            var line = await messages.Reader.ReadAsync().ConfigureAwait(false);
            using var document = JsonDocument.Parse(line);
            if (document.RootElement.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException("Python 服务消息必须是 JSON 对象。");
            return document.RootElement.Clone();
        }
        catch (ChannelClosedException error)
        {
            var cause = terminalError ?? error.InnerException ?? error;
            throw new IOException("Python 服务已关闭输出管道。" + ProcessExitTail() + DiagnosticTail, cause);
        }
        catch (Exception error) when (error is JsonException or InvalidDataException)
        {
            FailOutput(error);
            throw new IOException("Python 服务输出了无效 JSON。" + DiagnosticTail, error);
        }
    }

    private async Task PumpStdoutAsync()
    {
        Exception? failure = null;
        var reader = process.StandardOutput;
        var chunk = new char[4096];
        var line = new StringBuilder(4096);
        var oversized = false;
        try
        {
            while (true)
            {
                var count = await reader.ReadAsync(chunk.AsMemory()).ConfigureAwait(false);
                if (count == 0)
                {
                    if (line.Length > 0 || oversized)
                    {
                        failure = new EndOfStreamException("Python 服务在 JSON 行结束前关闭输出。");
                        FailOutput(failure);
                    }
                    break;
                }
                for (var index = 0; index < count; index++)
                {
                    var character = chunk[index];
                    if (character == '\n')
                    {
                        if (oversized)
                        {
                            oversized = false;
                            line.Clear();
                            continue;
                        }
                        if (discardOutput)
                        {
                            line.Clear();
                            continue;
                        }
                        if (line.Length > 0 && line[^1] == '\r') line.Length--;
                        try { await messages.Writer.WriteAsync(line.ToString()).ConfigureAwait(false); }
                        catch (ChannelClosedException) { discardOutput = true; }
                        line.Clear();
                        continue;
                    }
                    if (discardOutput) continue;
                    if (oversized) continue;
                    if (line.Length == MaxResponseChars)
                    {
                        failure = new InvalidDataException("Python 响应超过 18 MiB transport 限制。");
                        FailOutput(failure);
                        oversized = true;
                        line.Clear();
                    }
                    else line.Append(character);
                }
            }
            try
            {
                if (process.HasExited)
                {
                    exited = true;
                    if (process.ExitCode != 0)
                        failure ??= new IOException($"Python 服务退出（{process.ExitCode}）。" + DiagnosticTail);
                }
            }
            catch (InvalidOperationException) { }
            if (failure is null)
                failure = new EndOfStreamException("Python 服务正常结束了输出管道。");
            if (!stopping && !discardOutput) FailOutput(failure);
            else SetTerminalError(failure);
        }
        catch (Exception error)
        {
            failure = error;
            FailOutput(error);
        }
        finally
        {
            if (failure is not null) SetTerminalError(failure);
            messages.Writer.TryComplete(failure);
        }
    }

    private async Task DrainErrorsAsync()
    {
        var buffer = new char[2048];
        try
        {
            int count;
            while ((count = await process.StandardError.ReadAsync(buffer.AsMemory()).ConfigureAwait(false)) > 0)
            {
                lock (diagnosticGate)
                {
                    diagnostics.Append(buffer, 0, count);
                    if (diagnostics.Length > MaxDiagnosticChars)
                        diagnostics.Remove(0, diagnostics.Length - MaxDiagnosticChars);
                }
            }
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    public Task CloseAsync()
    {
        lock (closeGate) return closing ??= CloseCoreAsync();
    }

    private async Task CloseCoreAsync()
    {
        var closeDeadline = Stopwatch.StartNew();
        stopping = true;
        var writerAcquired = false;
        var closeTimedOut = false;
        var remaining = ShutdownTimeout - closeDeadline.Elapsed;
        try
        {
            if (remaining <= TimeSpan.Zero) throw new TimeoutException();
            if (!await inputWriter.WaitAsync(remaining).ConfigureAwait(false)) throw new TimeoutException();
            writerAcquired = true;
        }
        catch (TimeoutException) { closeTimedOut = true; }

        Exception? closeError = null;
        try
        {
            if (!writerAcquired)
            {
                stopWriting.Cancel();
                await TerminateOwnedProcessAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false);
                if (await inputWriter.WaitAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false))
                    writerAcquired = true;
                else
                    closeTimedOut = true;
            }

            if (writerAcquired)
            {
                stopWriting.Cancel();
                try { process.StandardInput.Close(); }
                catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException)
                { closeError = error; }
                finally
                {
                    inputWriter.Release();
                    writerAcquired = false;
                }
            }

            if (!closeTimedOut)
            {
                remaining = ShutdownTimeout - closeDeadline.Elapsed;
                if (remaining <= TimeSpan.Zero) throw new TimeoutException();
                await process.WaitForExitAsync().WaitAsync(remaining).ConfigureAwait(false);
                exited = true;
            }
        }
        catch (TimeoutException)
        {
            closeTimedOut = true;
            await TerminateOwnedProcessAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false);
        }
        try
        {
            if (writerAcquired) inputWriter.Release();
            if (await serial.WaitAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false))
                serial.Release();
            else
            {
                closeTimedOut = true;
                FailOutput(new TimeoutException("RPC 清理未在关闭期限内结束。"));
            }
        }
        finally
        {
            try { await stdoutPump.WaitAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false); }
            catch (TimeoutException) { closeTimedOut = true; }
            catch { }
            try { await stderrPump.WaitAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false); }
            catch (TimeoutException) { closeTimedOut = true; }
            catch { }
        }
        if (closeTimedOut)
            throw new IOException("Python 服务退出或管道收尾超时；本次连接不可复用。" + DiagnosticTail, terminalError);
        if (forcedTermination)
            throw new IOException("Python 服务在请求传输失败后被终止；连接不可复用。" + DiagnosticTail, terminalError);
        if (transportFailed)
            throw new IOException("Python 服务协议已失效；连接不可复用。" + DiagnosticTail, terminalError);
        if (process.ExitCode != 0)
            throw new IOException($"Python 服务退出失败（{process.ExitCode}）。" + DiagnosticTail, terminalError);
        if (closeError is not null) throw new IOException("关闭 Python 输入管道失败，服务进程已结束。", closeError);
    }

    private async Task TerminateOwnedProcessAsync(TimeSpan? waitTimeout = null)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                forcedTermination = true;
            }
        }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
        try
        {
            await process.WaitForExitAsync().WaitAsync(waitTimeout ?? ShutdownTimeout).ConfigureAwait(false);
            exited = true;
        }
        catch (TimeoutException error)
        {
            throw new IOException("已请求终止本次自有 Python 进程树，但进程未在清理期限内退出。", error);
        }
        catch (InvalidOperationException) { exited = true; }
    }

    private string ProcessExitTail()
    {
        try { return process.HasExited ? $" Exit code: {process.ExitCode}. " : string.Empty; }
        catch (ObjectDisposedException) { return string.Empty; }
        catch (InvalidOperationException) { return string.Empty; }
    }

    private void SetTerminalError(Exception error) => Interlocked.CompareExchange(ref terminalError, error, null);

    private void FailOutput(Exception error)
    {
        SetTerminalError(error);
        transportFailed = true;
        discardOutput = true;
        messages.Writer.TryComplete(error);
    }

    private static bool TryGetString(JsonElement value, string property, out string result)
    {
        if (value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out var field) &&
            field.ValueKind == JsonValueKind.String)
        {
            result = field.GetString()!;
            return true;
        }
        result = string.Empty;
        return false;
    }

    private static void RequireAbsoluteDirectory(string path, string name)
    {
        if (!Path.IsPathFullyQualified(path) || !Directory.Exists(path))
            throw new DirectoryNotFoundException($"{name} 必须是已存在的绝对目录：{path}");
    }

    private static TimeSpan NormalizeTimeout(TimeSpan timeout) =>
        TimeSpan.FromMilliseconds(Math.Clamp(timeout.TotalMilliseconds, 1, 60_000));

    public async ValueTask DisposeAsync()
    {
        try { await CloseAsync().ConfigureAwait(false); }
        finally
        {
            exited = true;
            process.Dispose();
            stopWriting.Dispose();
        }
    }

    private sealed record TerminalResponse(bool Ok, JsonElement Result, string? ErrorCode, string? ErrorMessage);
}

public enum WorkerWriteFailureKind
{
    CallerCancelled,
    RequestDeadline,
    ConnectionClosing,
}

public sealed class WorkerWriteInterruptedException(WorkerWriteFailureKind failureKind, string message, Exception innerException)
    : IOException(message, innerException)
{
    public WorkerWriteFailureKind FailureKind { get; } = failureKind;
}
