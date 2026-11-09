using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using OpenCvSharp;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;
using StarMap.Vision;

/// <summary>Explicit local evidence collector. Never activates a window, injects input, or writes runtime identity.</summary>
internal sealed class MapOpenCollector
{
    const int MaximumEvents = 2048;
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static readonly (string Season, string Mode)[] Contexts =
        [("s0", "nightmare"), ("s0", "difficult"), ("s1", "nightmare"), ("s1", "difficult")];
    readonly object gate = new();
    readonly object labelReadGate = new();
    readonly SemaphoreSlim signal = new(0, 1);
    readonly CancellationTokenSource stopping = new();
    readonly List<Trigger> events = [];
    readonly List<object> sourceIssues = [];
    readonly WindowsClientCapture capture = new();
    readonly GlobalInputBindings bindings = GlobalInputBindings.FromSettings("G", SettingsDocument.ActionOrder.Select(_ => "").ToArray());
    readonly string output, labelFile;
    readonly long handle;
    readonly DateTimeOffset utcAnchor = DateTimeOffset.UtcNow;
    readonly long qpcAnchor = Stopwatch.GetTimestamp();
    readonly GlobalInputDispatcher dispatcher;
    BoundWindow bound = null!;
    UserLabel? currentLabel, lastValidLabel;
    Trigger? pending, active;
    CancellationTokenSource? activeCancellation;
    string? stopReason, labelError;
    bool draining, storageFailed, eventLogFailed;
    int escapes, repeatTargetG, injectedTargetG, rejectedTargetG, eventLimitRejectedTargetG, flushed;
    long sourceGeneration;

    sealed record UserLabel(string Season, string Mode, long Generation);
    sealed class Trigger(long sequence, GlobalInputKeyEvent input, DateTimeOffset eventUtc, UserLabel? label)
    {
        public long Sequence { get; } = sequence;
        public GlobalInputKeyEvent Input { get; } = input;
        public DateTimeOffset EventUtc { get; } = eventUtc;
        public UserLabel? Label { get; } = label;
        public string Status { get; set; } = "pending";
        public string? Reason { get; set; }
        public DateTimeOffset? FinishedAt { get; set; }
        public int Attempts { get; set; }
        public string? SamplePath { get; set; }
        public string? ExpectedSamplePath { get; set; }
        public string? CaptureRecordPath { get; set; }
        public bool PngCompleted { get; set; }
        public bool CaptureRecordCompleted { get; set; }
        public CaptureMetadata? Metadata { get; set; }
        public object? Evidence { get; set; }
        public List<object> StorageErrors { get; } = [];
        public string? StatusBeforeStorageFailure { get; set; }
        public bool AppendFailed { get; set; }
        public object? Details { get; set; }
        public bool Written { get; set; }
    }

    MapOpenCollector(string output, string labelFile, long handle)
    { this.output = output; this.labelFile = labelFile; this.handle = handle; dispatcher = new(bindings); }

    internal static int Run(string[] args)
    {
        string Required(string name)
        {
            int at = Array.IndexOf(args, name);
            return at >= 0 && at + 1 < args.Length ? args[at + 1] : throw new ArgumentException(name + " is required");
        }
        string output = Path.GetFullPath(Required("--output"));
        if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Preserve prior evidence; --output must be a new directory.");
        Directory.CreateDirectory(output);
        var collector = new MapOpenCollector(output, Path.GetFullPath(Required("--label-file")),
            long.Parse(Required("--capture-window"), CultureInfo.InvariantCulture));
        return collector.RunAsync().GetAwaiter().GetResult();
    }

    async Task<int> RunAsync()
    {
        WindowsGlobalInput? source = null;
        Task worker = Task.CompletedTask;
        GlobalInputStopResult? stopped = null;
        int exit = 0;
        ConsoleCancelEventHandler cancel = (_, e) => { e.Cancel = true; RequestStop("console-cancel"); };
        Console.CancelKeyPress += cancel;
        try
        {
            var candidates = WindowsClientCapture.EnumerateCandidates(128);
            var games = candidates.Items.Where(item => item.ProcessName == "dwrg" && item.Title == "第五人格").ToArray();
            if (games.Length != 1 || games[0].Handle != (nint)handle || candidates.Truncated)
                throw new InvalidOperationException("Expected exactly one current dwrg / 第五人格 candidate matching --capture-window; no binding or capture performed.");
            bound = capture.Bind(games[0]);
            RefreshLabel();
            if (Volatile.Read(ref currentLabel) is null) throw new InvalidDataException("Initial label unavailable: " + labelError);
            source = new(bindings);
            source.ObservationReceived += Receive;
            worker = Task.Run(async () =>
            {
                try { await WorkerAsync(); }
                catch (Exception failure)
                {
                    lock (gate) AddIssue("worker-failed", failure.GetType().Name + ": " + failure.Message);
                    RequestStop("worker-failed");
                    throw;
                }
            });
            var start = await source.StartAsync();
            if (!start.Started || !start.KeyboardAvailable || start.Snapshot is null)
                throw new InvalidOperationException("Native keyboard hook did not become ready: " + start.Error);
            // Started delivery precedes key delivery on the source's serial subscriber stream.
            Console.WriteLine(JsonSerializer.Serialize(new { marker = "MAP_WATCH_READY", nativeKeyboardHook = true,
                start.Generation, start.MouseAvailable, window = Identity(), userLabel = Volatile.Read(ref currentLabel),
                output, stopFile = Path.Combine(output, "stop.request"), initialCapture = false,
                scope = "pass-through G only; full-client local PNG; observation does not grant map identity" }));
            while (!stopping.IsCancellationRequested)
            {
                if (File.Exists(Path.Combine(output, "stop.request"))) { RequestStop("stop-file"); break; }
                RefreshLabel();
                await Task.Delay(75);
            }
        }
        catch (Exception error)
        {
            exit = 1;
            lock (gate) AddIssue("collector-failed", error.GetType().Name + ": " + error.Message);
            RequestStop("collector-failed");
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
        }
        finally
        {
            RequestStop(stopReason ?? "collector-finished");
            if (source is not null)
            {
                // No timeout is treated as success: retain ownership and await actual native cleanup.
                do { stopped = await source.StopAsync(TimeSpan.FromSeconds(2)); }
                while (!stopped.Stopped);
                source.ObservationReceived -= Receive;
                await source.DisposeAsync();
            }
            lock (gate) { draining = true; CancelPending("collector-stopped"); }
            Pulse();
            try { await worker; }
            catch (Exception failure) { exit = 1; Console.Error.WriteLine("Worker failed: " + failure.Message); }
            capture.Dispose();
            Console.CancelKeyPress -= cancel;
            if (stopped?.Error is not null) { lock (gate) AddIssue("source-stop-error", stopped.Error); exit = 1; }
            if (storageFailed) exit = 1;
            if (!await WriteSummaryAsync(stopped, exit)) exit = 1;
            WriteFallback(new { marker = "MAP_WATCH_STOPPED", exit, stopReason,
                sourceStopped = source is null || stopped?.Stopped == true, workerDrained = worker.IsCompletedSuccessfully,
                acceptedG = events.Count, savedImages = events.Count(e => e.PngCompleted), storageFailed, output });
        }
        return exit;
    }

    void Receive(GlobalInputNotification notification)
    {
        // This runs on the production serial dispatcher, not the native hook. No I/O, capture, delay or wait.
        lock (gate)
        {
            if (notification.Kind == GlobalInputNotificationKind.Started && notification.Snapshot is { } snapshot)
            { dispatcher.Reset(bindings, snapshot); sourceGeneration = snapshot.Generation; return; }
            if (notification.Kind is GlobalInputNotificationKind.Failed or GlobalInputNotificationKind.Stopped)
            {
                if (notification.Snapshot is { } reset) dispatcher.Reset(bindings, reset);
                if (!stopping.IsCancellationRequested)
                { AddIssue(notification.Code ?? "source-stopped", notification.Message); RequestStop("native-source-stopped"); }
                return;
            }
            if (notification.Kind == GlobalInputNotificationKind.Warning)
            { AddIssue(notification.Code ?? "source-warning", notification.Message); return; }
            if (notification.Key is not { } key) return;
            var scope = WindowsGlobalInput.ReadScope(handle, true, false);
            var dispatch = dispatcher.Dispatch(key, scope, WindowsGlobalInput.MonotonicSeconds);
            if (stopping.IsCancellationRequested || !key.IsDown || key.ForegroundWindow != handle || scope.CurrentForegroundWindow != handle) return;
            // Native flags: LLKHF_INJECTED / LLKHF_LOWER_IL_INJECTED. Do not log any other key/application.
            if (key.VirtualKey is not (0x47 or 0x1b)) return;
            if ((key.NativeFlags & 0x12) != 0) { if (key.VirtualKey == 0x47) injectedTargetG++; return; }
            if (key.IsRepeat) { if (key.VirtualKey == 0x47) repeatTargetG++; return; }
            if (dispatch.Actions.Contains("escape")) { escapes++; CancelPending("canceled-by-escape"); Pulse(); return; }
            if (key.VirtualKey != 0x47) return;
            if (!dispatch.Actions.Contains("map")) { rejectedTargetG++; return; }
            if (events.Count == MaximumEvents) { eventLimitRejectedTargetG++; AddIssue("event-limit", "2048 accepted G events reached; this target G was rejected at the explicit limit and the source is stopping."); RequestStop("event-limit"); return; }
            CancelPending("superseded-by-newer-g");
            var trigger = new Trigger(events.Count + 1, key, UtcAt(key.CreatedAtSeconds), Volatile.Read(ref currentLabel));
            events.Add(trigger);
            if (trigger.Label is null) Finish(trigger, "miss", "label-unavailable");
            else pending = trigger;
            Pulse();
        }
    }

    void CancelPending(string reason)
    {
        if (pending is { } waiting) { Finish(waiting, "canceled", reason); pending = null; }
        if (active is { } running && running.FinishedAt is null)
        { running.Reason ??= reason; activeCancellation?.Cancel(); }
    }

    void RequestStop(string reason)
    {
        lock (gate)
        {
            stopReason ??= reason;
            if (!stopping.IsCancellationRequested) stopping.Cancel();
            CancelPending("collector-stopped");
        }
        Pulse();
    }

    void Pulse() { if (signal.CurrentCount == 0) try { signal.Release(); } catch (SemaphoreFullException) { } }
    void AddIssue(string code, string? message) { if (sourceIssues.Count < 32) sourceIssues.Add(new { code, message, recordedAt = DateTimeOffset.UtcNow }); }
    static void Finish(Trigger trigger, string status, string? reason)
    { trigger.Status = status; trigger.Reason = reason; trigger.FinishedAt = DateTimeOffset.UtcNow; }

    void RefreshLabel()
    {
        // Serialize file readers separately; never hold the input gate during disk I/O.
        lock (labelReadGate)
        {
        UserLabel? next = null;
        string? error = null;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(labelFile));
            var root = document.RootElement;
            next = new(root.GetProperty("season").GetString() ?? "", root.GetProperty("mode").GetString() ?? "", root.GetProperty("generation").GetInt64());
            if (!Contexts.Contains((next.Season, next.Mode)) || next.Generation <= 0) throw new InvalidDataException("Expected s0/s1, nightmare/difficult and positive generation.");
            var previous = Volatile.Read(ref lastValidLabel);
            if (previous is not null && next != previous && next.Generation <= previous.Generation)
                throw new InvalidDataException("Changed label requires a strictly newer generation.");
        }
        catch (Exception failure) when (failure is IOException or UnauthorizedAccessException or JsonException or KeyNotFoundException or InvalidOperationException or InvalidDataException or FormatException or OverflowException)
        { error = failure.Message; next = null; }
        lock (gate)
        {
            labelError = error;
            if (next is not null) Volatile.Write(ref lastValidLabel, next);
            if (currentLabel != next) { Volatile.Write(ref currentLabel, next); CancelPending("label-changed-during-event"); Pulse(); }
        }
        }
    }

    async Task WorkerAsync()
    {
        while (true)
        {
            Trigger? request;
            CancellationTokenSource? cancel;
            lock (gate)
            {
                request = pending; pending = null; active = request;
                cancel = request is null ? null : new CancellationTokenSource(); activeCancellation = cancel;
            }
            if (request is not null)
            {
                try { await CaptureEventAsync(request, cancel!.Token); }
                catch (OperationCanceledException) { lock (gate) Finish(request, "canceled", request.Reason ?? "collector-stopped"); }
                catch (Exception failure)
                {
                    lock (gate) { Finish(request, "miss", (failure as ClientCaptureException)?.Code ?? failure.GetType().Name);
                        request.Details = new { failure.Message, geometry = (failure as ClientCaptureException)?.Geometry,
                            statistics = (failure as ClientCaptureException)?.Statistics }; }
                }
                finally { lock (gate) { active = null; activeCancellation = null; } cancel!.Dispose(); }
            }
            await FlushEventsAsync();
            lock (gate) if (draining && pending is null && active is null) break;
            await signal.WaitAsync(100);
        }
    }

    async Task CaptureEventAsync(Trigger trigger, CancellationToken token)
    {
        double target = trigger.Input.CreatedAtSeconds + .1;
        double wait = target - WindowsGlobalInput.MonotonicSeconds;
        if (wait > 0) await Task.Delay(TimeSpan.FromSeconds(wait), token);
        RefreshLabel();
        CapturedClientFrame? final = null;
        JsonObject? observations = null;
        bool? mapOpen = null;
        string? lastCaptureFailure = null;
        do
        {
            token.ThrowIfCancellationRequested();
            if (trigger.Label != Volatile.Read(ref currentLabel)) throw new OperationCanceledException("Label changed", token);
            capture.ValidateBinding(bound);
            trigger.Attempts++;
            try
            {
                var frame = await capture.CaptureAsync(bound, token);
                capture.ValidateForCommit(frame);
                var observed = Observe(frame);
                capture.ValidateForCommit(frame);
                final = frame; observations = observed;
                mapOpen = observed[trigger.Label!.Season + "/" + trigger.Label.Mode]?["mapOpen"]?.GetValue<bool>();
                if (mapOpen == true) break;
            }
            catch (ClientCaptureException failure) when (failure.Code == "game-client-dc-returned-blank-frame")
            { lastCaptureFailure = failure.Code; }
            double remaining = trigger.Input.CreatedAtSeconds + 1.05 - WindowsGlobalInput.MonotonicSeconds;
            if (remaining <= 0) break;
            await Task.Delay(TimeSpan.FromSeconds(Math.Min(.05, remaining)), token);
        } while (WindowsGlobalInput.MonotonicSeconds <= trigger.Input.CreatedAtSeconds + 1.05);
        token.ThrowIfCancellationRequested();
        if (final is null) { lock (gate) Finish(trigger, "miss", lastCaptureFailure ?? "no-usable-frame"); return; }
        capture.ValidateForCommit(final);
        RefreshLabel();
        if (trigger.Label != Volatile.Read(ref currentLabel)) throw new OperationCanceledException("Label changed", token);
        string folder = "event-" + trigger.Sequence.ToString("D6", CultureInfo.InvariantCulture);
        var capturedAt = utcAnchor + TimeSpan.FromSeconds((final.Metadata.PixelsCopiedQpc - qpcAnchor) / (double)final.Metadata.QpcFrequency);
        var evidence = new { eventKind = "physical-g-client-capture", evidenceKind = "live-client-capture", trigger.Sequence,
            userLabel = trigger.Label, labelAtCommit = Volatile.Read(ref currentLabel), labelIsUserSupplied = true,
            rawEvent = RawEvent(trigger), capturedAt, recordedAt = DateTimeOffset.UtcNow,
            window = Identity(), bindingGeneration = bound.Generation, final.Metadata, trigger.Attempts,
            mapOpen, mapStatus = mapOpen == true ? "observed-open" : mapOpen == false ? "negative-not-open" : "unconfirmed",
            observations, samplePath = "client.png", imageScope = "Full physical target client area only; no title bar, taskbar, resizing or pixel edits",
            observationScope = "Four independent read-only contexts; S1 StructureCandidate is detector permission, never map identity or a user label inference",
            timeScope = "capturedAt derives from actual PixelsCopiedQpc and startup UTC/QPC anchor; event UTC likewise derived from source monotonic time" };
        if (!await StoreCaptureAsync(trigger, final.Image, final.Metadata, evidence, folder)) return;
        lock (gate)
        {
            trigger.Details = new { capturedAt, mapOpen, mapStatus = evidence.mapStatus, labelAtCommit = evidence.labelAtCommit };
            // Cancellation during disk encoding cannot erase an already copied frame; keep both provenance and reason.
            Finish(trigger, trigger.Reason is null ? "saved" : "saved-before-cancellation", trigger.Reason ?? (mapOpen == true ? "observed-map-open" : mapOpen == false ? "negative-map-not-open" : "map-unconfirmed"));
        }
    }

    // Shared by the live worker and the real-filesystem boundary entry. Establish provenance before any disk write.
    async Task<bool> StoreCaptureAsync(Trigger trigger, BitmapSource image, CaptureMetadata metadata, object evidence, string folder)
    {
        string samplePath = folder + "/client.png", recordPath = folder + "/capture.json";
        lock (gate)
        {
            trigger.ExpectedSamplePath = samplePath;
            trigger.CaptureRecordPath = recordPath;
            trigger.Metadata = metadata;
            trigger.Evidence = evidence;
        }
        string phase = "png-write-failed";
        try
        {
            Directory.CreateDirectory(Path.Combine(output, folder));
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image));
            using (var file = new FileStream(Path.Combine(output, samplePath), FileMode.CreateNew)) encoder.Save(file);
            // Only a completed encoder and closed stream authorize a usable sample path.
            lock (gate) { trigger.PngCompleted = true; trigger.SamplePath = trigger.ExpectedSamplePath; }
            phase = "capture-record-write-failed";
            byte[] record = JsonSerializer.SerializeToUtf8Bytes(evidence, Json);
            await using (var file = new FileStream(Path.Combine(output, recordPath), FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                await file.WriteAsync(record);
            lock (gate) trigger.CaptureRecordCompleted = true;
            return true;
        }
        catch (Exception failure)
        {
            StorageFailure(trigger, phase, failure);
            return false;
        }
    }

    void StorageFailure(Trigger? trigger, string phase, Exception failure)
    {
        lock (gate)
        {
            storageFailed = true;
            var error = new { phase, type = failure.GetType().Name, failure.Message, recordedAt = DateTimeOffset.UtcNow };
            if (trigger is not null)
            {
                trigger.StatusBeforeStorageFailure ??= trigger.Status;
                trigger.StorageErrors.Add(error);
                Finish(trigger, "storage-failed", phase);
            }
            AddIssue(phase, failure.GetType().Name + ": " + failure.Message);
            RequestStop("storage-failed");
        }
        WriteFallback(new { marker = "MAP_WATCH_STORAGE_FAILED", phase, sequence = trigger?.Sequence, failure.Message });
    }

    static void WriteFallback(object value)
    {
        try { Console.WriteLine(JsonSerializer.Serialize(value)); }
        catch (Exception) { /* Console may also be unavailable; keep the same evidence in the final summary attempt. */ }
    }

    static JsonObject Observe(CapturedClientFrame frame)
    {
        using var bgra = new Mat(frame.Metadata.After.Height, frame.Metadata.After.Width, MatType.CV_8UC4);
        Marshal.Copy(frame.Bgra32Pixels.ToArray(), 0, bgra.Data, frame.Bgra32Pixels.Length);
        using var bgr = new Mat(); Cv2.CvtColor(bgra, bgr, ColorConversionCodes.BGRA2BGR);
        var result = new JsonObject();
        foreach (var context in Contexts)
        {
            try
            {
                var item = FloorObservation.Observe(bgr, context.Season, context.Mode, s1Use: S1FloorUse.StructureCandidate);
                result[context.Season + "/" + context.Mode] = new JsonObject { ["mapOpen"] = item.MapOpen,
                    ["floorId"] = item.FloorId, ["mapObservation"] = item.MapObservation.DeepClone(), ["floorRead"] = item.FloorRead.DeepClone(),
                    ["grantsMapIdentity"] = false };
            }
            catch (Exception failure) { result[context.Season + "/" + context.Mode] = new JsonObject { ["mapOpen"] = null, ["error"] = failure.GetType().Name + ": " + failure.Message }; }
        }
        return result;
    }

    object Identity() => new { handle = bound.Identity.Handle.ToInt64(), bound.Identity.ProcessId, bound.Identity.ThreadId,
        bound.Identity.ProcessStartUtcTicks, bound.Identity.WindowClass, bound.Identity.ProcessName, bound.Identity.Title };
    DateTimeOffset UtcAt(double monotonic) => utcAnchor + TimeSpan.FromSeconds(monotonic - qpcAnchor / (double)Stopwatch.Frequency);
    static object RawEvent(Trigger trigger) => new { createdAtMonotonicSeconds = trigger.Input.CreatedAtSeconds,
        createdAtUtc = trigger.EventUtc, trigger.Input.Generation, trigger.Input.NativeFlags,
        eventForegroundWindow = trigger.Input.ForegroundWindow, virtualKey = "G", isRepeat = trigger.Input.IsRepeat,
        trigger.Input.PassedToSystem, trigger.Input.Consumed };

    async Task FlushEventsAsync()
    {
        Trigger[] ready;
        lock (gate) ready = events.Where(e => e.FinishedAt is not null && !e.Written && !e.AppendFailed).ToArray();
        foreach (var item in ready)
        {
            try
            {
                if (eventLogFailed)
                    throw new IOException("A prior append failed; this terminal is retained in summary/stdout instead of appending after a possibly partial JSONL record.");
                object record;
                lock (gate) record = EventRecord(item);
                await File.AppendAllTextAsync(Path.Combine(output, "events.jsonl"), JsonSerializer.Serialize(record) + Environment.NewLine);
                // A later append failure cannot mark this or any other unwritten event as durable.
                lock (gate) { item.Written = true; flushed++; }
                WriteFallback(record);
            }
            catch (Exception failure)
            {
                bool priorFailure = eventLogFailed;
                lock (gate) { item.AppendFailed = true; eventLogFailed = true; }
                StorageFailure(item, priorFailure ? "event-log-unavailable-after-failure" : "event-log-append-failed", failure);
                object record;
                lock (gate) record = EventRecord(item);
                WriteFallback(new { marker = "MAP_WATCH_EVENT_FALLBACK", record });
            }
        }
    }

    static object EventRecord(Trigger item) => new { item.Sequence, rawEvent = RawEvent(item), userLabel = item.Label,
        item.Status, item.Reason, item.StatusBeforeStorageFailure, item.Attempts, item.ExpectedSamplePath, item.SamplePath,
        item.CaptureRecordPath, pngCompleted = item.PngCompleted, captureRecordCompleted = item.CaptureRecordCompleted,
        item.Metadata, item.Evidence, storageErrors = item.StorageErrors.ToArray(), item.AppendFailed, item.FinishedAt, item.Details };

    object Summary(GlobalInputStopResult? sourceStop, int exit) => new { startedAt = utcAnchor,
        finishedAt = DateTimeOffset.UtcNow, exit = storageFailed ? 1 : exit, stopReason, sourceGeneration, sourceStop, storageFailed,
        window = bound is null ? null : Identity(), bindingGeneration = bound?.Generation,
        workerDrained = events.All(e => e.FinishedAt is not null), eventLimit = MaximumEvents, acceptedG = events.Count, terminalEventsWritten = flushed,
        savedImages = events.Count(e => e.PngCompleted), completedCaptureRecords = events.Count(e => e.CaptureRecordCompleted),
        outcomes = events.GroupBy(e => e.Status).ToDictionary(g => g.Key, g => g.Count()),
        unwrittenTerminalEvents = events.Where(e => e.FinishedAt is not null && !e.Written).Select(EventRecord).ToArray(),
        storageFailureEvents = events.Where(e => e.StorageErrors.Count > 0).Select(EventRecord).ToArray(),
        repeatTargetG, injectedTargetG, rejectedTargetG, eventLimitRejectedTargetG, escapes, currentUserLabel = Volatile.Read(ref currentLabel), labelError,
        sourceIssues, scope = "Explicit read-only G-triggered target client collection; no game activation, injected input, identity/session writes or automatic initial frame; no other keys/application events persisted" };

    async Task<bool> WriteSummaryAsync(GlobalInputStopResult? sourceStop, int exit)
    {
        try
        {
            if (!File.Exists(Path.Combine(output, "events.jsonl")))
                await using (var file = new FileStream(Path.Combine(output, "events.jsonl"), FileMode.CreateNew)) { }
        }
        catch (Exception failure) { StorageFailure(null, "event-log-create-failed", failure); }
        try
        {
            await File.WriteAllTextAsync(Path.Combine(output, "summary.json"), JsonSerializer.Serialize(Summary(sourceStop, exit), Json));
            return !storageFailed;
        }
        catch (Exception failure)
        {
            StorageFailure(null, "summary-write-failed", failure);
            WriteFallback(new { marker = "MAP_WATCH_SUMMARY_FALLBACK", summary = Summary(sourceStop, 1) });
            return false;
        }
    }

    internal static int CheckStorageBoundaries(string[] args) => CheckStorageBoundariesAsync(args).GetAwaiter().GetResult();

    static async Task<int> CheckStorageBoundariesAsync(string[] args)
    {
        int option = Array.IndexOf(args, "--output");
        if (option < 0 || option + 1 >= args.Length) throw new ArgumentException("--output is required for preserved synthetic storage evidence");
        string root = Path.GetFullPath(args[option + 1]);
        if (Directory.Exists(root) || File.Exists(root)) throw new IOException("Use a new storage-boundary output directory.");
        Directory.CreateDirectory(root);
        var checks = new List<(string Name, bool Passed)>();
        var collectors = new List<MapOpenCollector>();
        MapOpenCollector Collector(string name)
        {
            string path = Path.Combine(root, name); Directory.CreateDirectory(path);
            var collector = new MapOpenCollector(path, "unused-synthetic-label", 0); collectors.Add(collector); return collector;
        }
        Trigger Event(long sequence) => new(sequence,
            new GlobalInputKeyEvent(0x47, 0x47, true, Array.Empty<int>(), WindowsGlobalInput.MonotonicSeconds, 0, 1, false, false),
            DateTimeOffset.UtcNow, new UserLabel("s0", "nightmare", 1));
        void Check(string name, bool passed) { checks.Add((name, passed)); Console.WriteLine((passed ? "ok " : "FAIL ") + name); }
        byte[] pixels = [0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255, 255, 255, 255, 255];
        var image = BitmapSource.Create(2, 2, 96, 96, PixelFormats.Bgra32, null, pixels, 8); image.Freeze();
        var geometry = new ClientGeometry(0, 0, 2, 2, 96);
        long now = Stopwatch.GetTimestamp();
        var metadata = new CaptureMetadata("synthetic-storage-boundary", false, geometry, geometry, now, now, now,
            Stopwatch.Frequency, new FrameStatistics(127.5, 127.5, false));
        var evidence = new { evidenceKind = "synthetic-storage-boundary", Metadata = metadata,
            imageScope = "Synthetic 2x2 BGRA bitmap created by this test; no game, hook or capture source used" };
        async Task<bool> Store(MapOpenCollector collector, Trigger item, string folder)
        { collector.events.Add(item); return await collector.StoreCaptureAsync(item, image, metadata, evidence, folder); }
        try
        {
            var normal = Collector("normal"); var first = Event(1);
            bool saved = await Store(normal, first, "event-000001");
            if (saved) Finish(first, "saved", "synthetic-storage-boundary");
            await normal.FlushEventsAsync();
            bool summarySaved = await normal.WriteSummaryAsync(null, 0);
            using (var stream = File.OpenRead(Path.Combine(normal.output, "event-000001", "client.png")))
            {
                var decoded = BitmapDecoder.Create(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad).Frames[0];
                var converted = new FormatConvertedBitmap(decoded, PixelFormats.Bgra32, null, 0);
                byte[] readback = new byte[16]; converted.CopyPixels(readback, 8, 0);
                Check("normal-png-decoded-original-pixels", decoded.PixelWidth == 2 && decoded.PixelHeight == 2 && readback.SequenceEqual(pixels));
            }
            using (var record = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(normal.output, "event-000001", "capture.json"))))
                Check("normal-record-and-log-completed", saved && summarySaved && first.PngCompleted && first.CaptureRecordCompleted && first.Written &&
                    record.RootElement.GetProperty("Metadata").GetProperty("Backend").GetString() == metadata.Backend &&
                    File.ReadAllLines(Path.Combine(normal.output, "events.jsonl")).Length == 1 && !normal.stopping.IsCancellationRequested);

            var jsonFailure = Collector("json-target-directory"); var second = Event(2);
            Directory.CreateDirectory(Path.Combine(jsonFailure.output, "event-000002", "capture.json"));
            bool jsonSaved = await Store(jsonFailure, second, "event-000002");
            await jsonFailure.FlushEventsAsync();
            await jsonFailure.WriteSummaryAsync(null, 0);
            Check("json-failure-retains-completed-png-and-provenance", !jsonSaved && second.Status == "storage-failed" && second.PngCompleted &&
                !second.CaptureRecordCompleted && second.SamplePath == "event-000002/client.png" && File.Exists(Path.Combine(jsonFailure.output, second.SamplePath)) &&
                ReferenceEquals(second.Metadata, metadata) && ReferenceEquals(second.Evidence, evidence) && jsonFailure.stopping.IsCancellationRequested);
            using (var summary = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(jsonFailure.output, "summary.json"))))
                Check("json-failure-summary-counts-real-png", summary.RootElement.GetProperty("savedImages").GetInt32() == 1 &&
                    summary.RootElement.GetProperty("completedCaptureRecords").GetInt32() == 0 && summary.RootElement.GetProperty("exit").GetInt32() == 1 &&
                    summary.RootElement.GetProperty("storageFailureEvents")[0].GetProperty("Metadata").GetProperty("Backend").GetString() == metadata.Backend);

            var pngFailure = Collector("png-target-directory"); var third = Event(3);
            Directory.CreateDirectory(Path.Combine(pngFailure.output, "event-000003", "client.png"));
            bool pngSaved = await Store(pngFailure, third, "event-000003");
            Check("failed-png-never-authorizes-sample-path", !pngSaved && !third.PngCompleted && third.SamplePath is null &&
                third.ExpectedSamplePath == "event-000003/client.png" && ReferenceEquals(third.Metadata, metadata) && third.Status == "storage-failed");
            await pngFailure.FlushEventsAsync(); await pngFailure.WriteSummaryAsync(null, 0);

            var appendFailure = Collector("append-target-directory"); var fourth = Event(4); var fifth = Event(5);
            foreach (var item in new[] { fourth, fifth })
                if (await Store(appendFailure, item, "event-" + item.Sequence.ToString("D6", CultureInfo.InvariantCulture))) Finish(item, "saved", "synthetic-storage-boundary");
            Directory.CreateDirectory(Path.Combine(appendFailure.output, "events.jsonl"));
            await appendFailure.FlushEventsAsync();
            bool appendSummary = await appendFailure.WriteSummaryAsync(null, 0);
            Check("append-failure-does-not-mark-any-unwritten-event-durable", !appendSummary && appendFailure.flushed == 0 &&
                new[] { fourth, fifth }.All(item => !item.Written && item.AppendFailed && item.Status == "storage-failed" && item.PngCompleted &&
                    item.CaptureRecordCompleted && item.SamplePath is not null && ReferenceEquals(item.Metadata, metadata)) && appendFailure.stopping.IsCancellationRequested);
            using (var summary = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(appendFailure.output, "summary.json"))))
                Check("summary-retains-every-unwritten-terminal", summary.RootElement.GetProperty("unwrittenTerminalEvents").GetArrayLength() == 2 &&
                    summary.RootElement.GetProperty("terminalEventsWritten").GetInt32() == 0 && summary.RootElement.GetProperty("exit").GetInt32() == 1);

            var summaryFailure = Collector("summary-target-directory"); var sixth = Event(6);
            if (await Store(summaryFailure, sixth, "event-000006")) Finish(sixth, "saved", "synthetic-storage-boundary");
            await summaryFailure.FlushEventsAsync();
            Directory.CreateDirectory(Path.Combine(summaryFailure.output, "summary.json"));
            var originalOut = Console.Out; using var fallback = new StringWriter(CultureInfo.InvariantCulture);
            bool summaryResult;
            try { Console.SetOut(fallback); summaryResult = await summaryFailure.WriteSummaryAsync(null, 0); }
            finally { Console.SetOut(originalOut); }
            Check("summary-failure-reports-failure-and-stdout-fallback", !summaryResult && summaryFailure.storageFailed &&
                summaryFailure.stopping.IsCancellationRequested && fallback.ToString().Contains("MAP_WATCH_SUMMARY_FALLBACK", StringComparison.Ordinal) &&
                fallback.ToString().Contains("\"exit\":1", StringComparison.Ordinal));
            await File.WriteAllTextAsync(Path.Combine(summaryFailure.output, "stdout-fallback.jsonl"), fallback.ToString());
        }
        catch (Exception failure)
        { Check("boundary-execution-completed", false); Console.Error.WriteLine(failure); }
        finally { foreach (var collector in collectors) collector.capture.Dispose(); }
        await File.WriteAllTextAsync(Path.Combine(root, "storage-boundary-result.json"), JsonSerializer.Serialize(new {
            scope = "Shared actual collector storage functions; synthetic 2x2 image and real filesystem failures only; no native hook, input or game capture",
            passed = checks.All(c => c.Passed), checks = checks.Select(c => new { c.Name, c.Passed }) }, Json));
        return checks.All(c => c.Passed) ? 0 : 1;
    }

    /// <summary>Uses the production state and dispatcher, with no native hook, window, capture or file I/O.</summary>
    internal static int CheckBoundaries()
    {
        var bindings = GlobalInputBindings.FromSettings("G", SettingsDocument.ActionOrder.Select(_ => "").ToArray());
        var state = new GlobalInputState(); var dispatcher = new GlobalInputDispatcher(bindings);
        dispatcher.Reset(bindings, state.Reset(bindings, 9));
        var scope = new GlobalInputScope(42, true, 42, GameEnabled: true);
        var checks = new List<(string Name, bool Passed)>();
        GlobalInputDispatch Edge(bool down, long foreground = 42, double time = 100)
            => dispatcher.Dispatch(state.Transition(0x47, 0x22, 0, down, foreground, time)!, scope, 100);
        checks.Add(("physical-target-down-accepted", Edge(true).Actions.SequenceEqual(new[] { "map" })));
        checks.Add(("held-repeat-does-not-retrigger", Edge(true).Actions.Count == 0));
        Edge(false); checks.Add(("event-foreground-mismatch-rejected", Edge(true, 99).Actions.Count == 0));
        Edge(false); scope = scope with { CurrentForegroundWindow = 99 };
        checks.Add(("dispatch-foreground-mismatch-rejected", Edge(true).Actions.Count == 0));
        Edge(false); scope = scope with { CurrentForegroundWindow = 42 };
        checks.Add(("stale-event-rejected", Edge(true, time: 97).Actions.Count == 0));
        foreach (var check in checks) Console.WriteLine((check.Passed ? "ok " : "FAIL ") + check.Name);
        Console.WriteLine("Boundary scope: linked production pure input policy only; no native hooks or game capture used.");
        return checks.All(c => c.Passed) ? 0 : 1;
    }
}
