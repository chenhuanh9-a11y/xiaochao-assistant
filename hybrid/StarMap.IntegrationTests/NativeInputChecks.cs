using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using StarMap.Bridge;
using StarMap.Desktop;

/// <summary>Input facts and UI lifetime seams; no injected OS keys or game interaction.</summary>
internal static class NativeInputChecks
{
    private static int checks;
    private static void Check(bool value, string message)
    { if (!value) throw new InvalidOperationException(message); checks++; Console.WriteLine("PASS " + message); }

    public static async Task RunAsync()
    {
        var policy = new NativeInputPolicy([
            new("map", "G", 0x47, []), new("escape", "ESCAPE", 0x1b, []),
            new("toggle-panel", "F9", 0x78, []), new("confirm-round", "Ctrl+F8", 0x77, [0x11])]);
        policy.BeginSource(7, [0x47]);
        var now = Stopwatch.GetTimestamp();
        ShortcutIntent? Edge(int key, bool down = true, bool wasDown = false, bool game = true,
            long? stamp = null, long generation = 7, bool text = false) =>
            policy.Accept(new(key, key, down, wasDown, stamp ?? now, 1, generation), now, game, text);
        Check(Edge(0x47, wasDown: true) is null && policy.Sequence == 0,
            "key held before subscription cannot become a fresh opening through repeat");
        Edge(0x47, down: false);
        var first = Edge(0x47);
        Check(first is { Sequence: 1, OpenIntent: true }, "first fresh G produces one ordered open intent");
        Check(Edge(0x47, wasDown: true) is null && policy.Sequence == 1, "repeat keeps the same input phase and sequence");
        Check(Edge(0x1b) is { Sequence: 2, OpenIntent: false }, "Escape closes the physical phase with a new sequence");
        Edge(0xa2); // Physical left Ctrl
        Check(Edge(0x47) is null && Edge(0x77)?.Action == "confirm-round", "physical modifier facts match exact configured chords");
        Edge(0xa2, down: false);
        Check(Edge(0x47, game: false) is null && Edge(0x78, game: false)?.Action == "toggle-panel",
            "other applications never grant game input while management remains global");
        Check(Edge(0x47, stamp: now - Stopwatch.Frequency * 3) is null
            && Edge(0x47, stamp: now + Stopwatch.Frequency) is null
            && Edge(0x47, generation: 6) is null && Edge(0x47, text: true) is null,
            "old future foreign-generation and text-entry presses grant no action");
        Edge(0x5b);
        Check(Edge(0x47) is null, "Windows modifier never downgrades to a bare product shortcut");
        Edge(0x5b, down: false);
        policy.Reconcile(1, true);
        Check(!policy.OpenIntent, "older visual evidence cannot override a newer close edge");

        var peer = new Peer();
        var model = new RuntimeViewModel();
        await model.AttachAsync(peer, "unused-input-frame-root");
        await model.HandleMapInputAsync(1, true, 1, .01);
        Check(peer.Actions.Count == 0 && !model.HasRound, "outside-round map input neither prepares nor dispatches a round");
        peer.HoldStart = true;
        var start = model.StartRoundAsync();
        await peer.StartEntered.Task;
        await model.HandleMapInputAsync(2, false, 1, .01);
        Check(peer.Actions.SequenceEqual(new[] { "start_round" }) && !peer.StartCancelled,
            "map edge during explicit start leaves the start request and physical phase intact");
        peer.StartRelease.TrySetResult();
        await start;
        Check(model.RoundPreparing, "the production-owned preparing round is retained after start");
        var map = model.HandleMapInputAsync(3, true, 1, .01);
        await peer.MapEntered.Task;
        await model.HandleMapInputAsync(4, false, 1, .01);
        peer.MapRelease.TrySetResult();
        await map;
        Check(peer.MapCancelled && peer.Actions[^1] == "close_map" && model.HasRound
            && model.Snapshot?.GetProperty("mapOpen").GetBoolean() == false,
            "close supersedes an active opening, rejects its late success, and retains the round");
        var count = peer.Actions.Count;
        await model.HandleMapInputAsync(5, true, 2, .01);
        Check(peer.Actions.Count == count, "wrong bound HWND cannot dispatch a map intent");
        model.BeginClose();
        await model.HandleMapInputAsync(6, true, 1, .01);
        Check(peer.Actions.Count == count, "closing revokes subsequent input work");
        Console.WriteLine($"{checks} native input checks passed; synthetic key facts/RPC, actual policy and VM; no native keys/game acceptance.");
    }

    public static async Task RunLiveHostAsync(string sourceRoot, string python, string resources)
    {
        var data = Path.Combine(sourceRoot, "work", "hybrid-live-input-host", Guid.NewGuid().ToString("N"));
        var window = new MainWindow(new(sourceRoot, python, null, data, LiveCapture: true));
        var closed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        window.Closed += (_, _) => closed.TrySetResult();
        window.Show();
        try
        {
            await window.ConnectAsync(resources);
            // Read ownership facts solely to verify real disposal. No private
            // mutation, factory replacement, synthetic native events or pixels.
            T? Owned<T>(string name) where T : class => typeof(MainWindow)
                .GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(window) as T;
            var worker = Owned<IWorkerConnection>("worker");
            var input = Owned<NativeInputController>("nativeInput");
            Check(window.Runtime.IsLive && window.Runtime.InputStatus.Contains("全局快捷键已连接")
                && input is not null && worker is not null && !worker.HasExited,
                "actual MainWindow starts native Python pipe and the pass-through keyboard owner");
            Check(window.Runtime.BoundHwnd == 0 && !window.Runtime.CanStart && !window.Runtime.HasRound,
                "unbound native startup neither starts a round nor acquires a game frame");
            window.Close();
            await closed.Task.WaitAsync(TimeSpan.FromSeconds(15));
            Check(input!.Stopped && worker!.HasExited,
                $"actual window close drains its keyboard thread and Python process pid={worker?.ProcessId}");
            Console.WriteLine("Native input host: real MainWindow/hook/native pipe, unbound metadata only; no injected keys, pixels or game acceptance.");
        }
        finally
        {
            if (!closed.Task.IsCompleted) { window.Close(); await closed.Task.WaitAsync(TimeSpan.FromSeconds(15)); }
        }
    }

    private sealed class Peer : IWorkerConnection
    {
        public List<string> Actions { get; } = [];
        public TaskCompletionSource StartEntered { get; } = new();
        public TaskCompletionSource StartRelease { get; } = new();
        public TaskCompletionSource MapEntered { get; } = new();
        public TaskCompletionSource MapRelease { get; } = new();
        public bool HoldStart, StartCancelled, MapCancelled;
        private bool preparing, open;
        public int ProcessId => 0;
        public bool HasExited => false;
        public bool SupportsRuntime => true;
        public string EvidenceKind => "live-client-capture";
        public Task<WorkerHello> HelloAsync(CancellationToken cancellation = default) =>
            Task.FromResult(new WorkerHello([new("s0", "difficult", "fixture")], "fixture"));
        public async Task<JsonElement> InvokeAsync(string operation, object? arguments = null, CancellationToken cancellation = default)
        {
            if (operation == "dispatch")
            {
                var action = JsonSerializer.SerializeToElement(arguments).GetProperty("action").GetString()!;
                Actions.Add(action);
                if (action == "start_round")
                {
                    StartEntered.TrySetResult();
                    using var registration = cancellation.Register(() => StartCancelled = true);
                    if (HoldStart) await StartRelease.Task;
                    preparing = true;
                }
                if (action == "map")
                {
                    MapEntered.TrySetResult();
                    using var registration = cancellation.Register(() => MapCancelled = true);
                    await MapRelease.Task;
                    return Result(true); // Deliberately late success must be rejected by the UI owner.
                }
                if (action == "close_map") open = false;
            }
            return Result(open);
        }
        private JsonElement Result(bool mapOpen) => JsonSerializer.SerializeToElement(new
        {
            capability = new { available = true },
            snapshot = new { season = "s0", mode = "difficult", capture = new { hwnd = "1" }, roundId = 1,
                roundActive = false, roundPreparing = preparing, mapOpen,
                availableCommands = new[] { "start_round", "map", "capture_now", "close_map", "end_round" } }
        });
        public Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default) => throw new NotSupportedException();
        public Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default) => throw new NotSupportedException();
        public Task CloseAsync() => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
