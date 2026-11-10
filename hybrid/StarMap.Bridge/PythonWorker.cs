using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Bridge;

/// <summary>Owns one worker. Serial RPC keeps both queues bounded; stderr is drained independently.</summary>
public sealed class PythonWorker : IAsyncDisposable
{
    private readonly Process process;
    private readonly SemaphoreSlim serial = new(1, 1);
    private readonly SemaphoreSlim inputWriter = new(1, 1);
    private readonly CancellationTokenSource stopWriting = new();
    private readonly Task stderrPump;
    private readonly Queue<string> recentErrors = new();
    private readonly TimeSpan requestTimeout;
    private readonly object closeGate = new();
    private Task? closing;
    private long nextId;
    private volatile bool stopping;
    private bool exited;
    private Exception? terminalError;
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };

    private PythonWorker(Process process, TimeSpan timeout)
    {
        this.process = process;
        requestTimeout = timeout;
        stderrPump = DrainErrorsAsync();
    }

    public int ProcessId => process.Id;
    public bool HasExited => exited || process.HasExited;
    public string DiagnosticTail { get { lock (recentErrors) return string.Join(Environment.NewLine, recentErrors); } }

    public static PythonWorker Start(string python, string workerScript, string resourceRoot,
        string? installationRoot = null, TimeSpan? requestTimeout = null)
    {
        if (!Path.IsPathFullyQualified(workerScript) || !File.Exists(workerScript))
            throw new FileNotFoundException("找不到 Python 资源服务，请选择当前源码根目录。", workerScript);
        if (!Path.IsPathFullyQualified(resourceRoot) || !Directory.Exists(resourceRoot))
            throw new DirectoryNotFoundException("请选择实际存在的资源目录。");
        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = Path.GetDirectoryName(workerScript)!
        };
        foreach (var arg in new[] { "-B", "-u", workerScript, "--resource-root", resourceRoot })
            start.ArgumentList.Add(arg);
        if (installationRoot is not null)
        {
            start.ArgumentList.Add("--installation-root");
            start.ArgumentList.Add(installationRoot);
        }
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        var process = Process.Start(start) ?? throw new IOException("无法启动 Python 资源服务。");
        return new PythonWorker(process, requestTimeout ?? TimeSpan.FromSeconds(30));
    }

    public Task<WorkerHello> HelloAsync(CancellationToken cancellation = default) =>
        CallAsync<WorkerHello>("hello", null, cancellation);

    public Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default) =>
        CallAsync<BrowseCatalog>("catalog", new { season, mode }, cancellation);

    public Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default) =>
        CallAsync<BrowsePreview>("preview", new { catalogId, key }, cancellation);

    private async Task<T> CallAsync<T>(string operation, object? arguments, CancellationToken cancellation)
    {
        await serial.WaitAsync(cancellation).ConfigureAwait(false);
        try
        {
            cancellation.ThrowIfCancellationRequested();
            if (stopping) throw new ObjectDisposedException(nameof(PythonWorker));
            if (terminalError is not null) throw new IOException("Python 服务已断开，请重新连接资源。", terminalError);
            var id = ++nextId;
            var request = arguments is null ? new JsonObject() : JsonSerializer.SerializeToNode(arguments)!.AsObject();
            request["id"] = id;
            request["operation"] = operation;
            // After transmission, always drain this reply even if the caller changes selection.
            // The next request must never consume an old reply. UI cancellation revokes display authority.
            using var deadline = new CancellationTokenSource(requestTimeout);
            JsonDocument response;
            try
            {
                using (var writeDeadline = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token, stopWriting.Token))
                {
                    await inputWriter.WaitAsync(writeDeadline.Token).ConfigureAwait(false);
                    try
                    {
                        await process.StandardInput.WriteLineAsync(request.ToJsonString().AsMemory(), writeDeadline.Token).ConfigureAwait(false);
                        await process.StandardInput.FlushAsync(writeDeadline.Token).ConfigureAwait(false);
                    }
                    finally { inputWriter.Release(); }
                }
                var line = await process.StandardOutput.ReadLineAsync(deadline.Token).ConfigureAwait(false);
                if (line is null) throw new EndOfStreamException("Python 服务已退出。" + DiagnosticTail);
                if (line.Length > 18 * 1024 * 1024) throw new InvalidDataException("Python 响应超过预览大小限制。");
                response = JsonDocument.Parse(line);
            }
            catch (Exception error) when (error is IOException or JsonException or OperationCanceledException or InvalidOperationException)
            {
                terminalError = error;
                throw new IOException("读取 Python 服务失败，请重新连接资源。", error);
            }
            using (response)
            {
                var root = response.RootElement;
                if (!root.TryGetProperty("id", out var actualId) || !actualId.TryGetInt64(out var actual) || actual != id)
                {
                    terminalError = new InvalidDataException("Python 响应与当前请求不一致。");
                    throw terminalError;
                }
                cancellation.ThrowIfCancellationRequested();
                if (!root.GetProperty("ok").GetBoolean())
                {
                    var error = root.GetProperty("error");
                    throw new WorkerException(error.GetProperty("code").GetString()!, error.GetProperty("message").GetString()!);
                }
                return root.GetProperty("result").Deserialize<T>(Json) ?? throw new InvalidDataException("Python 返回空结果。");
            }
        }
        finally { serial.Release(); }
    }

    private async Task DrainErrorsAsync()
    {
        // Read chunks: a single unterminated traceback must not grow an unbounded line buffer.
        var buffer = new char[2048];
        int count;
        while ((count = await process.StandardError.ReadAsync(buffer).ConfigureAwait(false)) > 0)
        {
            lock (recentErrors)
            {
                recentErrors.Enqueue(new string(buffer, 0, count));
                while (recentErrors.Count > 8) recentErrors.Dequeue();
            }
        }
    }

    public Task CloseAsync()
    {
        lock (closeGate) return closing ??= CloseCoreAsync();
    }

    private async Task CloseCoreAsync()
    {
        stopping = true;
        // EOF is the normal stop path, including a currently executing read-only request.
        // StreamWriter.Close cannot race its own asynchronous write/flush.
        stopWriting.Cancel();
        await inputWriter.WaitAsync().ConfigureAwait(false);
        Exception? closeError = null;
        try { process.StandardInput.Close(); }
        catch (IOException error) { closeError = error; }
        finally { inputWriter.Release(); }
        try
        {
            await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
            exited = true;
        }
        catch (TimeoutException)
        {
            process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync().ConfigureAwait(false);
            exited = true;
            throw new IOException("Python 服务未在退出期限内结束，已终止本次自有服务进程。");
        }
        finally
        {
            await serial.WaitAsync().ConfigureAwait(false);
            serial.Release();
            await stderrPump.ConfigureAwait(false);
        }
        if (process.ExitCode != 0) throw new IOException($"Python 服务退出失败（{process.ExitCode}）。" + DiagnosticTail);
        if (closeError is not null) throw new IOException("关闭 Python 输入管道失败，服务进程已结束。", closeError);
    }

    public async ValueTask DisposeAsync()
    {
        try { await CloseAsync().ConfigureAwait(false); }
        finally { process.Dispose(); }
    }
}
