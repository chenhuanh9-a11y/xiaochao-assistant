using System.Collections.Frozen;
using System.Text.Json;

namespace StarMap.Core.Session;

/// <summary>Opaque runtime identities; no field is a recognition grant.</summary>
public sealed record RuntimeContext(
    string Season = "s0", string Mode = "nightmare", string ProfileId = "",
    string? RoundId = null, string? MapId = null, string? FloorId = null,
    string? ResourceId = null, string? FrameId = null)
{
    public SessionSelection Selection => new(Season, Mode, ProfileId);
}

public sealed record SessionSelection(string Season = "s0", string Mode = "nightmare", string ProfileId = "");

public sealed record SessionRequestContext(RequestTicket Ticket, RuntimeContext Runtime);

/// <summary>Immutable copy of state actually observed by the injected runtime.</summary>
public sealed class RuntimeSnapshot
{
    public RuntimeSnapshot(RuntimeContext context, bool engineConnected = false,
        IEnumerable<string>? availableCommands = null, bool roundActive = false,
        bool roundPreparing = false, bool roundWorkActive = false,
        bool liveEngineAvailable = false, bool liveAuthorization = false,
        bool manualMapPickAvailable = false, string message = "", JsonElement? state = null)
    {
        Context = context ?? throw new ArgumentNullException(nameof(context));
        EngineConnected = engineConnected;
        AvailableCommands = (availableCommands ?? []).ToFrozenSet(StringComparer.Ordinal);
        RoundActive = roundActive;
        RoundPreparing = roundPreparing;
        RoundWorkActive = roundWorkActive;
        LiveEngineAvailable = liveEngineAvailable;
        LiveAuthorization = liveAuthorization;
        ManualMapPickAvailable = manualMapPickAvailable;
        Message = message;
        State = state is { ValueKind: not JsonValueKind.Undefined } value
            ? value.Clone() : JsonSerializer.SerializeToElement(new { });
    }

    public RuntimeContext Context { get; }
    public bool EngineConnected { get; }
    public IReadOnlySet<string> AvailableCommands { get; }
    public bool RoundActive { get; }
    public bool RoundPreparing { get; }
    public bool RoundWorkActive { get; }
    public bool LiveEngineAvailable { get; }
    public bool LiveAuthorization { get; }
    public bool ManualMapPickAvailable { get; }
    public string Message { get; }
    public JsonElement State { get; }

    internal RuntimeSnapshot Withheld(bool disconnected = false) => new(
        Context with { MapId = null, FloorId = null, FrameId = null },
        !disconnected && EngineConnected, disconnected ? [] : AvailableCommands,
        RoundActive, RoundPreparing, RoundWorkActive,
        !disconnected && LiveEngineAvailable, liveAuthorization: false,
        manualMapPickAvailable: !disconnected && ManualMapPickAvailable, message: Message);
}

/// <summary>A receipt reports an operation, never identity, pose, or display authority.</summary>
public sealed record OperationReceipt(
    string Action, bool Success, SessionRequestContext RequestContext,
    RuntimeContext? ResultContext = null, string Reason = "", string Message = "");

public sealed record RuntimeOperationResult(OperationReceipt Receipt, RuntimeSnapshot Snapshot);

public sealed class SessionRuntimeRequest
{
    public SessionRuntimeRequest(string action, JsonElement payload, SessionRequestContext context)
    {
        Action = action;
        Payload = payload.Clone();
        Context = context;
    }

    public string Action { get; }
    public JsonElement Payload { get; }
    public SessionRequestContext Context { get; }
}

/// <summary>
/// No implementation is supplied until the real live runtime is migrated.
/// ReadSnapshot must be bounded and safe on the owning UI thread. DispatchAsync
/// executes off that thread, serially, and must return its real committed snapshot.
/// Cancellation is cooperative; a non-cooperating dispatch keeps the worker busy.
/// </summary>
public interface ISessionRuntime
{
    RuntimeSnapshot ReadSnapshot();
    ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request, CancellationToken cancellationToken);
}

public interface IRuntimeRecoveryControl
{
    void CancelMapOpenRecovery(string reason);
}

/// <summary>Private bounded cache work cannot return a recognition receipt or publish state.</summary>
public interface ISessionMaintenanceRuntime
{
    ValueTask RememberRenderMaterialAsync(string renderToken, RuntimeContext lifetime, CancellationToken cancellationToken);
}

public interface ISessionDispatcher
{
    bool CheckAccess();
    void Post(Action action);
}

/// <summary>The supplied context, not Core, chooses the UI toolkit.</summary>
public sealed class SynchronizationContextSessionDispatcher : ISessionDispatcher
{
    private readonly SynchronizationContext _context;
    private readonly int _ownerThread = Environment.CurrentManagedThreadId;

    public SynchronizationContextSessionDispatcher(SynchronizationContext context)
        => _context = context ?? throw new ArgumentNullException(nameof(context));

    public bool CheckAccess() => Environment.CurrentManagedThreadId == _ownerThread;
    public void Post(Action action) => _context.Post(_ => action(), null);
}

public enum SessionDispatchDisposition { Enqueued, Coalesced, Unavailable, ResourceLocked, Closed, Superseded }
public sealed record SessionDispatchResult(SessionDispatchDisposition Disposition, UiActionToken? Token = null);
public sealed record SessionFailure(string Action, string Phase, string Reason);
public sealed record SessionCompletion(string Action, RequestTicket Ticket, string Disposition);

public sealed record SessionView(
    SessionSnapshot Sequence, SessionSelection DesiredSelection, SessionSelection AppliedSelection,
    RuntimeSnapshot Runtime, bool ContextPending, bool Busy, string PendingAction,
    bool WorkerActive, int QueuedCount, bool ResourceLocked, string Message,
    OperationReceipt? LastReceipt, SessionFailure? LastFailure, long StaleResults,
    IReadOnlyList<UiActionSnapshot> Actions);
