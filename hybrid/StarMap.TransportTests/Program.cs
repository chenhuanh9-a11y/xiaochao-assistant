using System.Text.Json;
using StarMap.Bridge;

internal static class Program
{
    private static int checks;

    private static void Check(bool value, string description)
    {
        if (!value) throw new InvalidOperationException(description);
        Console.WriteLine("PASS " + description);
        checks++;
    }

    private static async Task<int> Main(string[] args)
    {
        if (args.Length != 5)
        {
            Console.Error.WriteLine("usage: StarMap.TransportTests SOURCE_ROOT PYTHON FIXTURE_RESOURCE_ROOT ISOLATED_DATA_ROOT FRAME_ROOT");
            return 2;
        }

        var sourceRoot = Path.GetFullPath(args[0]);
        var python = args[1];
        var resourceRoot = Path.GetFullPath(args[2]);
        var dataRoot = Path.GetFullPath(args[3]);
        var frameRoot = Path.GetFullPath(args[4]);
        var workerScript = Path.Combine(sourceRoot, "hybrid", "python", "runtime_worker.py");
        var worker = HybridWorker.Start(python, workerScript, resourceRoot, dataRoot, frameRoot);
        try
        {
            var hello = await worker.HelloAsync();
            Check(hello.Operations?.Contains("prepare") == true && worker.SupportsRuntime,
                "actual hello advertises runtime support");
            Check(hello.Contexts.Any(context => context.Season == "s0" && context.Mode == "nightmare"),
                "unified worker exposes production browse contexts");

            var catalog = await worker.CatalogAsync("s0", "nightmare");
            var row = catalog.Rows.First(candidate => candidate.Available);
            var preview = await worker.PreviewAsync(catalog.CatalogId, row.Key);
            Check(Convert.FromBase64String(preview.PngBase64).AsSpan(0, 8)
                    .SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }),
                "production catalog and PNG preview cross the persistent process");

            var pid = worker.ProcessId;
            var prepared = await worker.InvokeAsync("prepare", new { season = "s0", mode = "nightmare" });
            Check(worker.ProcessId == pid && prepared.TryGetProperty("snapshot", out _),
                "prepare executes in the same Python process as browse");
            var snapshot = await worker.InvokeAsync("snapshot");
            Check(worker.ProcessId == pid && snapshot.TryGetProperty("snapshot", out _),
                "snapshot executes in the same Python process after prepare");

            await worker.CloseAsync();
            Check(worker.HasExited, "normal stdin EOF closes the owned process");
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
        }

        await CheckCancelledDrainAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot);
        await CheckReconnectAsync(workerScript, python, resourceRoot, dataRoot, frameRoot);
        await CheckStartupFailureAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot);
        await CheckLegacyCloseBackpressureAsync(sourceRoot, python, resourceRoot);
        await CheckWriteBlockedAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot,
            callerCancelsFirst: false);
        await CheckWriteBlockedAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot,
            callerCancelsFirst: true);
        await CheckTimeoutAndBusinessErrorReuseAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot);
        foreach (var mode in new[] { "bad-id-string", "bad-id-object", "bad-id-null" })
            await CheckMalformedIdAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot, mode);
        foreach (var mode in new[] { "bad-output", "bad-output-array", "bad-output-missing-event" })
            await CheckMalformedOutputDrainAsync(sourceRoot, python, resourceRoot, dataRoot, frameRoot, mode);
        Console.WriteLine($"{checks} transport checks passed; real runtime_worker and fixture resources, no game/native input.");
        return 0;
    }

    private static async Task CheckCancelledDrainAsync(string sourceRoot, string python, string resourceRoot,
        string dataRoot, string frameRoot)
    {
        var proxy = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "delayed_runtime_proxy.py");
        var oldWorker = Environment.GetEnvironmentVariable("HYBRID_TEST_REAL_WORKER");
        var oldDelay = Environment.GetEnvironmentVariable("HYBRID_TEST_DELAY_RESULT_MS");
        var oldInject = Environment.GetEnvironmentVariable("HYBRID_TEST_INJECT_STALE");
        Environment.SetEnvironmentVariable("HYBRID_TEST_REAL_WORKER", Path.Combine(sourceRoot, "hybrid", "python", "runtime_worker.py"));
        Environment.SetEnvironmentVariable("HYBRID_TEST_DELAY_RESULT_MS", "500");
        Environment.SetEnvironmentVariable("HYBRID_TEST_INJECT_STALE", "1");
        var worker = HybridWorker.Start(python, proxy, resourceRoot, dataRoot, frameRoot);
        try
        {
            Check((await worker.HelloAsync()).Operations?.Length > 0,
                "foreign session and prior id terminal events cannot complete hello");
            var actualCatalog = await worker.CatalogAsync("s0", "nightmare");
            var actualRow = actualCatalog.Rows.First(candidate => candidate.Available);
            using var cancellation = new CancellationTokenSource();
            var pending = worker.PreviewAsync(actualCatalog.CatalogId, actualRow.Key, cancellation.Token);
            await Task.Delay(80);
            cancellation.Cancel();
            try
            {
                await pending;
                throw new InvalidOperationException("in-flight cancellation returned the delayed success result");
            }
            catch (OperationCanceledException) { Check(true, "cancelled caller never receives delayed success"); }
            var next = await worker.PreviewAsync(actualCatalog.CatalogId, actualRow.Key);
            Check(next.Key == actualRow.Key, "old terminal is drained before the next RPC is released");
            await worker.CloseAsync();
            Check(worker.HasExited, "delayed proxy receives normal EOF and exits");
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_TEST_REAL_WORKER", oldWorker);
            Environment.SetEnvironmentVariable("HYBRID_TEST_DELAY_RESULT_MS", oldDelay);
            Environment.SetEnvironmentVariable("HYBRID_TEST_INJECT_STALE", oldInject);
        }
    }

    private static async Task CheckReconnectAsync(string script, string python, string resourceRoot,
        string dataRoot, string frameRoot)
    {
        await using var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot);
        Check((await worker.HelloAsync()).Contexts.Length > 0, "fresh connection succeeds after a closed process");
        await worker.CloseAsync();
    }

    private static async Task CheckStartupFailureAsync(string sourceRoot, string python,
        string resourceRoot, string dataRoot, string frameRoot)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "startup_fail.py");
        var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot);
        try
        {
            try
            {
                await worker.HelloAsync();
                throw new InvalidOperationException("nonzero startup unexpectedly returned hello");
            }
            catch (IOException error) when (error.Message.Contains("Python 服务", StringComparison.Ordinal))
            {
                Check(true, "nonzero startup fails the pending request with process diagnostics");
            }
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
        }
        Check(worker.HasExited, "failed startup remains queryable after dispose");
    }

    private static async Task CheckLegacyCloseBackpressureAsync(string sourceRoot, string python, string resourceRoot)
    {
        var proxy = Path.Combine(sourceRoot, "hybrid", "StarMap.IntegrationTests", "delayed_worker.py");
        var marker = Path.Combine(Path.GetTempPath(), "starmap-legacy-preview-" + Guid.NewGuid().ToString("N") + ".txt");
        var previousMarker = Environment.GetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER");
        var previousOperation = Environment.GetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER_OPERATION");
        Environment.SetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER", marker);
        Environment.SetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER_OPERATION", "preview");
        var worker = PythonWorker.Start(python, proxy, resourceRoot);
        try
        {
            var catalog = await worker.CatalogAsync("s0", "nightmare");
            var row = catalog.Rows.First(candidate => candidate.Available);
            using var cancellation = new CancellationTokenSource();
            var pending = worker.PreviewAsync(catalog.CatalogId, row.Key, cancellation.Token);
            await WaitUntilAsync(() => File.Exists(marker));
            cancellation.Cancel();
            var closing = worker.CloseAsync();
            try
            {
                await pending;
                throw new InvalidOperationException("close-time cancelled preview unexpectedly returned success");
            }
            catch (OperationCanceledException) { }
            await closing;
            Check(worker.HasExited,
                "close waits for the submitted large preview response before EOF without killing its worker");
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER", previousMarker);
            Environment.SetEnvironmentVariable("HYBRID_DELAYED_WORKER_MARKER_OPERATION", previousOperation);
            if (File.Exists(marker)) File.Delete(marker);
        }
    }

    private static async Task WaitUntilAsync(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (!condition())
        {
            if (DateTime.UtcNow >= deadline) throw new TimeoutException("Delayed worker did not receive the preview request.");
            await Task.Delay(1);
        }
    }

    private static async Task CheckWriteBlockedAsync(string sourceRoot, string python, string resourceRoot,
        string dataRoot, string frameRoot, bool callerCancelsFirst)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "transport_fault_worker.py");
        var mode = callerCancelsFirst ? "writeblock-cancel" : "writeblock-deadline";
        var previousMode = Environment.GetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE");
        Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", mode);
        var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot,
            requestTimeout: callerCancelsFirst ? TimeSpan.FromSeconds(5) : TimeSpan.FromMilliseconds(200));
        try
        {
            await worker.HelloAsync();
            using var cancellation = new CancellationTokenSource(callerCancelsFirst ? 200 : 300);
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            var pending = worker.InvokeAsync("snapshot", new { padding = new string('x', 60_000) }, cancellation.Token);
            WorkerWriteInterruptedException? interrupted = null;
            try
            {
                await pending.WaitAsync(TimeSpan.FromSeconds(3));
                throw new InvalidOperationException("blocked JSONL write unexpectedly completed");
            }
            catch (WorkerWriteInterruptedException error) { interrupted = error; }
            Console.WriteLine($"blocked write {mode}: {interrupted?.FailureKind}, {elapsed.ElapsedMilliseconds} ms");
            Check(interrupted?.FailureKind == (callerCancelsFirst
                    ? WorkerWriteFailureKind.CallerCancelled
                    : WorkerWriteFailureKind.RequestDeadline),
                callerCancelsFirst
                    ? "caller cancellation interrupts blocked write and reports its cause"
                    : "request deadline interrupts blocked write before later caller cancellation");
            Check(worker.HasExited, "interrupted partial request terminates its own worker process");
            try
            {
                await worker.InvokeAsync("snapshot");
                throw new InvalidOperationException("failed connection accepted a later request");
            }
            catch (IOException) { Check(true, "failed partial-write connection cannot be reused"); }
            try { await worker.CloseAsync().WaitAsync(TimeSpan.FromSeconds(2)); }
            catch (IOException) { }
            Check(worker.HasExited, "forced write cleanup remains observable as exited");
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", previousMode);
        }
    }

    private static async Task CheckTimeoutAndBusinessErrorReuseAsync(string sourceRoot, string python,
        string resourceRoot, string dataRoot, string frameRoot)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "transport_fault_worker.py");
        var previousMode = Environment.GetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE");
        Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", "timeout-drain");
        var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot,
            requestTimeout: TimeSpan.FromMilliseconds(100));
        try
        {
            try
            {
                await worker.InvokeAsync("slow");
                throw new InvalidOperationException("slow operation unexpectedly beat its deadline");
            }
            catch (TimeoutException) { Check(true, "request deadline cancels and drains the original terminal"); }
            try
            {
                await worker.InvokeAsync("reject");
                throw new InvalidOperationException("business rejection unexpectedly succeeded");
            }
            catch (WorkerException error) when (error.Code == "expected-rejection")
            { Check(true, "business WorkerException leaves the connection usable"); }
            var snapshot = await worker.InvokeAsync("snapshot");
            Check(snapshot.GetProperty("operation").GetString() == "snapshot",
                "drained request timeout and business error both allow later RPCs");
            await worker.CloseAsync();
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", previousMode);
        }
    }

    private static async Task CheckMalformedOutputDrainAsync(string sourceRoot, string python, string resourceRoot,
        string dataRoot, string frameRoot, string mode)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "transport_fault_worker.py");
        var previousMode = Environment.GetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE");
        Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", mode);
        var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot);
        try
        {
            try
            {
                await worker.HelloAsync();
                throw new InvalidOperationException("invalid protocol event unexpectedly completed hello");
            }
            catch (IOException error) when (error.Message.Contains("无效", StringComparison.Ordinal))
            { Check(true, $"{mode} fails the active request explicitly"); }

            var close = worker.CloseAsync();
            try { await close.WaitAsync(TimeSpan.FromSeconds(2)); }
            catch (IOException) { }
            Check(close.IsCompleted && worker.HasExited,
                $"{mode} closes the bounded queue, drains ten later events, and lets close finish");
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", previousMode);
        }
    }

    private static async Task CheckMalformedIdAsync(string sourceRoot, string python, string resourceRoot,
        string dataRoot, string frameRoot, string mode)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "StarMap.TransportTests", "transport_fault_worker.py");
        var previous = Environment.GetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE");
        Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", mode);
        var worker = HybridWorker.Start(python, script, resourceRoot, dataRoot, frameRoot);
        try
        {
            try { await worker.HelloAsync(); throw new Exception("malformed id was accepted"); }
            catch (IOException error) when (error.InnerException is InvalidDataException)
            { Check(true, $"{mode} fails as a protocol error"); }
            try { await worker.InvokeAsync("snapshot"); throw new Exception("malformed id connection was reused"); }
            catch (IOException) { Check(true, $"{mode} connection cannot be reused"); }
        }
        finally
        {
            try { await worker.DisposeAsync(); }
            catch (IOException) when (worker.HasExited) { }
            Environment.SetEnvironmentVariable("HYBRID_TRANSPORT_FAULT_MODE", previous);
        }
        Check(worker.HasExited, $"{mode} cleanup closes its own process");
    }
}
