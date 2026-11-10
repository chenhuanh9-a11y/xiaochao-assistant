namespace StarMap.Bridge;

/// <summary>Independent native callback lane owned by one Python worker.</summary>
public interface IWorkerCaptureService : IAsyncDisposable
{
    string PipeName { get; }
    void BindWorker(int processId);
    IDisposable BeginRequest(string session, long requestId, string operation, CancellationToken cancellation);
    // Connection close only. Ordinary cancellation is scoped by BeginRequest's token.
    void CancelPending();
}
