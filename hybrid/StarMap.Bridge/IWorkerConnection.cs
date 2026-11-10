using System.Text.Json;

namespace StarMap.Bridge;

/// <summary>One serialized connection to the Python worker process.</summary>
public interface IWorkerConnection : IAsyncDisposable
{
    int ProcessId { get; }
    bool HasExited { get; }
    bool SupportsRuntime { get; }
    Task<WorkerHello> HelloAsync(CancellationToken cancellation = default);
    Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default);
    Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default);
    Task<JsonElement> InvokeAsync(string operation, object? arguments = null, CancellationToken cancellation = default);
    Task CloseAsync();
}
