using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace HybridGrid;

static class Program
{
    static readonly object StateLock = new(), OutputLock = new();
    static readonly BlockingCollection<SearchWork> Work = new(1);
    static SearchWork? active;

    sealed class SearchWork : IDisposable
    {
        public required string Session, ReferencePath;
        public required int Id, Width, Height, TimeoutMs;
        public required JsonObject Input;
        public readonly Stopwatch Clock = Stopwatch.StartNew();
        public readonly CancellationTokenSource Computation = new(), Timeout = new();
        public CancellationTokenRegistration TimeoutRegistration;
        public string? CancellationReason;

        public void Dispose()
        {
            TimeoutRegistration.Dispose();
            Timeout.Dispose();
            Computation.Dispose();
        }
    }

    sealed class ReferenceCache
    {
        // The dedicated worker alone validates and replaces this one-entry cache.
        string? path;
        long length;
        DateTime lastWrite;
        ushort[]? field;

        public ushort[] Load(SearchWork work)
        {
            var token = work.Computation.Token;
            token.ThrowIfCancellationRequested();
            string resolved = Path.GetFullPath(work.ReferencePath);
            var info = new FileInfo(resolved);
            long required = checked((long)work.Width * work.Height * sizeof(ushort));
            if (info.Length != required || required > int.MaxValue)
                throw new InvalidDataException("reference-dimensions-mismatch");
            if (path == resolved && length == info.Length && lastWrite == info.LastWriteTimeUtc && field is not null)
                return field;

            byte[] bytes = new byte[(int)required];
            using (var stream = new FileStream(resolved, new FileStreamOptions
            {
                Mode = FileMode.Open,
                Access = FileAccess.Read,
                Share = FileShare.Read,
                Options = FileOptions.Asynchronous | FileOptions.SequentialScan
            }))
            {
                int read = 0;
                while (read < bytes.Length)
                {
                    token.ThrowIfCancellationRequested();
                    int count = stream.ReadAsync(
                        bytes.AsMemory(read, Math.Min(65536, bytes.Length - read)), token)
                        .AsTask().GetAwaiter().GetResult();
                    if (count == 0) throw new EndOfStreamException("reference-truncated");
                    read += count;
                }
                if (stream.Length != required) throw new InvalidDataException("reference-size-changed");
            }

            token.ThrowIfCancellationRequested();
            var loaded = new ushort[bytes.Length / 2];
            Buffer.BlockCopy(bytes, 0, loaded, 0, bytes.Length);
            token.ThrowIfCancellationRequested();
            path = resolved;
            length = info.Length;
            lastWrite = info.LastWriteTimeUtc;
            field = loaded;
            return loaded;
        }
    }

    static void Emit(object value)
    {
        string line = JsonSerializer.Serialize(value);
        lock (OutputLock)
        {
            Console.Out.WriteLine(line);
            Console.Out.Flush();
        }
    }

    static void Cancel(SearchWork work, string reason)
    {
        lock (StateLock)
        {
            if (!ReferenceEquals(active, work)) return;
            work.CancellationReason ??= reason;
            work.Computation.Cancel();
        }
    }

    static void RunWorker()
    {
        var cache = new ReferenceCache();
        foreach (var request in Work.GetConsumingEnumerable())
        {
            double loadMs = 0, kernelMs = 0;
            var phaseClock = Stopwatch.StartNew();
            bool loading = true;
            object terminal;
            try
            {
                Emit(new { @event = "started", session = request.Session, id = request.Id });
                phaseClock.Restart();
                var field = cache.Load(request);
                loadMs = phaseClock.Elapsed.TotalMilliseconds;
                loading = false;
                phaseClock.Restart();
                var result = global::Grid.Search(
                    request.Input,
                    field,
                    () => request.Computation.IsCancellationRequested,
                    (phase, completedPoints) => Emit(new
                    {
                        @event = "progress",
                        session = request.Session,
                        id = request.Id,
                        phase,
                        completedPoints
                    }));
                kernelMs = phaseClock.Elapsed.TotalMilliseconds;
                request.Computation.Token.ThrowIfCancellationRequested();
                terminal = new
                {
                    @event = "result",
                    session = request.Session,
                    id = request.Id,
                    ok = true,
                    rows = result.Rows,
                    loadMs,
                    kernelMs,
                    stages = result.Stages,
                    elapsedMs = request.Clock.Elapsed.TotalMilliseconds
                };
            }
            catch (OperationCanceledException)
            {
                if (loading) loadMs = phaseClock.Elapsed.TotalMilliseconds;
                else kernelMs = phaseClock.Elapsed.TotalMilliseconds;
                terminal = CancelledResult(request, loadMs, kernelMs);
            }
            catch (Exception error)
            {
                if (loading) loadMs = phaseClock.Elapsed.TotalMilliseconds;
                else kernelMs = phaseClock.Elapsed.TotalMilliseconds;
                terminal = new
                {
                    @event = "result",
                    session = request.Session,
                    id = request.Id,
                    ok = false,
                    reason = "error",
                    errorType = error.GetType().Name,
                    message = error.Message,
                    loadMs,
                    kernelMs,
                    elapsedMs = request.Clock.Elapsed.TotalMilliseconds
                };
            }

            // Cancellation and terminal commit share a lock. A late cancel
            // cannot affect the next session or computation.
            lock (StateLock)
            {
                if (request.Computation.IsCancellationRequested)
                    terminal = CancelledResult(request, loadMs, kernelMs);
                Emit(terminal);
                if (ReferenceEquals(active, request)) active = null;
            }
            request.Dispose();
        }
    }

    static object CancelledResult(SearchWork request, double loadMs, double kernelMs)
        => new
        {
            @event = "result",
            session = request.Session,
            id = request.Id,
            ok = false,
            reason = request.CancellationReason ?? "cancelled",
            loadMs,
            kernelMs,
            elapsedMs = request.Clock.Elapsed.TotalMilliseconds
        };

    static SearchWork ParseSearch(JsonObject request)
    {
        string session = request["session"]?.GetValue<string>() ?? throw new InvalidDataException("session-required");
        int id = request["id"]?.GetValue<int>() ?? throw new InvalidDataException("id-required");
        int timeoutMs = request["timeoutMs"]?.GetValue<int>() ?? throw new InvalidDataException("timeout-required");
        if (timeoutMs < 0) throw new InvalidDataException("negative-timeout");
        string reference = request["referencePath"]?.GetValue<string>() ?? throw new InvalidDataException("reference-required");
        var input = request["input"]?.DeepClone().AsObject() ?? throw new InvalidDataException("input-required");
        int width = input["width"]?.GetValue<int>() ?? 0;
        int height = input["height"]?.GetValue<int>() ?? 0;
        if (width <= 0 || height <= 0 || checked((long)width * height * 2) > int.MaxValue)
            throw new InvalidDataException("invalid-reference-dimensions");

        double scale = input["scale"]?.GetValue<double>() ?? double.NaN;
        if (!double.IsFinite(scale) || scale <= 0 || !float.IsFinite((float)scale) || (float)scale <= 0)
            throw new InvalidDataException("invalid-scale");
        var points = input["points"]?.AsArray() ?? throw new InvalidDataException("points-required");
        if (points.Count == 0) throw new InvalidDataException("empty-points");
        foreach (var point in points)
        {
            var coordinates = point?.AsArray();
            if (coordinates?.Count != 2 || coordinates.Any(v => v is null
                || !double.IsFinite(v.GetValue<double>())
                || !float.IsFinite((float)v.GetValue<double>())))
                throw new InvalidDataException("invalid-point");
        }
        if (input["prior"] is { } prior)
        {
            var rows = prior.AsArray();
            if (rows.Count != 3 || rows.Any(row => row is not JsonArray array || array.Count != 3
                || array.Any(v => v is null || !double.IsFinite(v.GetValue<double>()))))
                throw new InvalidDataException("invalid-prior");
        }

        input["cancelled"] = input["cancelled"]?.GetValue<bool>() ?? false;
        return new()
        {
            Session = session,
            Id = id,
            ReferencePath = reference,
            Input = input,
            Width = width,
            Height = height,
            TimeoutMs = timeoutMs
        };
    }

    static void Handle(JsonObject request)
    {
        string op = request["op"]?.GetValue<string>() ?? throw new InvalidDataException("op-required");
        if (op == "cancel")
        {
            string session = request["session"]?.GetValue<string>() ?? throw new InvalidDataException("session-required");
            int id = request["id"]?.GetValue<int>() ?? throw new InvalidDataException("id-required");
            lock (StateLock)
                if (active is { } running && running.Session == session && running.Id == id)
                    Cancel(running, "cancelled");
            return;
        }
        if (op != "search") throw new InvalidDataException("unsupported-op");

        // Reject busy work before parsing a potentially large point array.
        lock (StateLock)
        {
            if (active is not null)
            {
                string busySession = request["session"]?.GetValue<string>() ?? throw new InvalidDataException("session-required");
                int busyId = request["id"]?.GetValue<int>() ?? throw new InvalidDataException("id-required");
                Emit(new { @event = "result", session = busySession, id = busyId, ok = false, reason = "busy" });
                return;
            }
        }

        var work = ParseSearch(request);
        lock (StateLock)
        {
            if (active is not null)
            {
                Emit(new { @event = "result", session = work.Session, id = work.Id, ok = false, reason = "busy" });
                work.Dispose();
                return;
            }
            active = work;
            work.TimeoutRegistration = work.Timeout.Token.Register(() => Cancel(work, "timeout"));
            work.Timeout.CancelAfter(work.TimeoutMs);
            if (work.Input["cancelled"]!.GetValue<bool>()) Cancel(work, "cancelled");
            if (!Work.TryAdd(work)) throw new InvalidOperationException("worker-queue-unavailable");
        }
    }

    public static void Main()
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        var worker = new Thread(RunWorker) { IsBackground = false, Name = "grid-computation" };
        worker.Start();
        Emit(new { ready = true, protocol = 2, pid = Environment.ProcessId });
        try
        {
            while (Console.ReadLine() is { } line)
            {
                JsonObject? request = null;
                try
                {
                    request = JsonNode.Parse(line)?.AsObject() ?? throw new InvalidDataException("object-required");
                    if (request["op"]?.GetValue<string>() == "stop") break;
                    Handle(request);
                }
                catch (Exception error)
                {
                    Emit(new
                    {
                        @event = "result",
                        session = request?["session"]?.DeepClone(),
                        id = request?["id"]?.DeepClone(),
                        ok = false,
                        reason = "error",
                        errorType = error.GetType().Name,
                        message = error.Message
                    });
                }
            }
        }
        finally
        {
            lock (StateLock)
                if (active is { } running) Cancel(running, "cancelled");
            Work.CompleteAdding();
            worker.Join();
            Work.Dispose();
        }
    }
}
