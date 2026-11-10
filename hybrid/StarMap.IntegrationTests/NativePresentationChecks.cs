using System.IO;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Bridge;
using StarMap.Desktop;

/// <summary>Native display consumer regressions with an explicit RPC/OS-context substitute.</summary>
internal static class NativePresentationChecks
{
    private static void Check(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
        Console.WriteLine("PASS " + message);
    }

    public static async Task RunAsync()
    {
        foreach (var loseAfterDecode in new[] { false, true })
        {
            var peer = new Peer();
            var model = new RuntimeViewModel { IsVisible = true };
            var calls = 0;
            var returned = false;
            model.NativeContextIsCurrent = _ => returned || (loseAfterDecode && ++calls == 1);
            await model.AttachAsync(peer, "unused-native-frame-root");
            Check(peer.Actions.SequenceEqual(new[] { "close_map" }) && model.ActiveRender is null && model.NativeMaterial is null,
                $"context loss {(loseAfterDecode ? "after" : "before")} decode revokes the backend and current native token");
            returned = true;
            model.IsVisible = false; model.IsVisible = true;
            await Task.Delay(30);
            Check(model.NativeMaterial is null && model.AlignedImage is null && model.ActiveRender is null,
                "returning to the same native context cannot resurrect a withdrawn material");
            model.BeginClose();
        }

        var source = new Peer();
        var live = new RuntimeViewModel { IsVisible = true, NativeContextIsCurrent = _ => true };
        await live.AttachAsync(source, "unused-native-frame-root");
        foreach (var action in new Func<Task>[] { live.CaptureAsync, live.SetFloorAsync, live.SelectCandidateAsync, live.ConfirmAsync })
        {
            var before = source.Actions.Count;
            var pending = action();
            await Task.Delay(30);
            Check(live.Busy && source.Actions.Count == before, "live frame action waits for return to the bound game");
            await live.CancelAsync();
            await pending;
            Check(source.Actions.Count == before + 1 && source.Actions[^1] == "close_map",
                "cancel during return-to-game delay sends no stale frame action");
        }
        live.BeginClose();
        Console.WriteLine("12 native presentation checks passed; simulated RPC/context, actual VM and PNG decode; no native capture/game acceptance.");
    }

    private sealed class Peer : IWorkerConnection
    {
        private bool hasRender = true;
        private readonly JsonElement render;
        public List<string> Actions { get; } = [];
        public int ProcessId => 0;
        public bool HasExited => false;
        public bool SupportsRuntime => true;
        public string EvidenceKind => "live-client-capture";
        public Peer()
        {
            var bitmap = BitmapSource.Create(1, 1, 96, 96, PixelFormats.Bgra32, null, new byte[] { 0, 0, 255, 255 }, 4);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var output = new MemoryStream(); encoder.Save(output);
            render = JsonSerializer.SerializeToElement(new { token = "old-token", season = "s0", mode = "difficult", mapId = "map", floor = "1",
                profileId = "p", effectiveProfileId = "p", frameId = "old-frame", generation = 1, roundId = 1, displayGeneration = 1,
                resourceIdentity = "r", width = 1, height = 1, clientWidth = 1, clientHeight = 1, originX = 0, originY = 0, captureDpi = 96,
                liveAuthorization = true, evidenceKind = EvidenceKind, pngBase64 = Convert.ToBase64String(output.ToArray()),
                matrix = new[] { new[] { 1, 0, 0 }, new[] { 0, 1, 0 }, new[] { 0, 0, 1 } } });
        }
        public Task<WorkerHello> HelloAsync(CancellationToken cancellation = default) =>
            Task.FromResult(new WorkerHello([new("s0", "difficult", "fixture")], "fixture"));
        public Task<JsonElement> InvokeAsync(string operation, object? arguments = null, CancellationToken cancellation = default)
        {
            cancellation.ThrowIfCancellationRequested();
            if (operation == "dispatch")
            {
                var action = JsonSerializer.SerializeToElement(arguments).GetProperty("action").GetString()!;
                Actions.Add(action);
                if (action == "close_map") hasRender = false;
            }
            object? material = hasRender ? render : null;
            return Task.FromResult(JsonSerializer.SerializeToElement(new { capability = new { available = true },
                snapshot = new { season = "s0", mode = "difficult", mapId = "map", floor = "1", frameId = "old-frame", roundId = 1,
                    capture = new { hwnd = "1" }, roundActive = true, roundPreparing = false, displayAuthorized = hasRender,
                    alignedVisible = hasRender, render = material, selectedCandidateIndex = 0, availableFloors = new[] { "1", "2" },
                    candidateRows = new[] { new { key = "map", mapId = "map", name = "map" } },
                    availableCommands = new[] { "capture_now", "set_floor", "select_candidate", "confirm_route" } }, render = material }));
        }
        public Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default) => throw new NotSupportedException();
        public Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default) => throw new NotSupportedException();
        public Task CloseAsync() => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
