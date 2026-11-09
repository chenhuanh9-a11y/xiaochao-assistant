using StarMap.Core.Session;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private static readonly TimeSpan RecoveryInterval = TimeSpan.FromSeconds(.2);
    private static readonly TimeSpan RecoveryLifetime = TimeSpan.FromSeconds(2);
    private long _lastMapInputSequence, _displayGeneration;
    private RecoveryTicket? _mapRecovery;

    private sealed record RecoveryScope(long Epoch, string? Round, long Cycle, SessionSelection Selection,
        long DisplayGeneration, BoundResources? Resources, CaptureBindingScope? Binding, long InputSequence);
    private sealed class RecoveryTicket(RecoveryScope scope, long started)
    {
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public RecoveryScope Scope { get; } = scope;
        public long Started { get; } = started;
        public TimeSpan Next { get; set; } = RecoveryInterval;
        public int Remaining { get; set; } = 8;
    }

    private RecoveryScope CurrentRecoveryScope() => new(_epoch, _roundId, _mapCycle, _selection,
        _displayGeneration, _resources, _capture.BindingScope, _lastMapInputSequence);

    private MapOpenRecoveryStatus RecoveryStatus()
    {
        if (_mapRecovery is not { } row) return new(false);
        bool current = !_closed && _roundActive && _capture.IsBound && row.Scope == CurrentRecoveryScope();
        var elapsed = _options.TimeProvider.GetElapsedTime(row.Started);
        bool pending = current && row.Remaining > 0 && elapsed < RecoveryLifetime;
        return new(pending, row.Id, pending && elapsed >= row.Next, row.Remaining,
            pending ? "waiting" : current ? "budget-exhausted" : "scope-changed");
    }

    private async ValueTask<RuntimeOperationResult> MapInput(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        bool open;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            long? sequence = null;
            if (request.Payload.TryGetProperty("inputSequence", out var input))
            {
                if (input.ValueKind != System.Text.Json.JsonValueKind.Number
                    || !input.TryGetInt64(out var number) || number <= _lastMapInputSequence)
                    return Receipt(request, false, "map-input-sequence-expired");
                sequence = number;
            }
            if (request.Payload.TryGetProperty("openIntent", out var intent))
            {
                if (intent.ValueKind is not (System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False))
                    return Receipt(request, false, "map-input-intent-invalid");
                open = intent.GetBoolean();
            }
            else open = !_mapOpen;
            if (sequence is { } accepted) _lastMapInputSequence = accepted;
        }
        return open ? await CaptureAndSolve(request, epoch, token).ConfigureAwait(false) : CloseMap(request);
    }

    private RuntimeOperationResult PreparationFailed(SessionRuntimeRequest request, long epoch, object diagnostic)
    {
        lock (_sync)
        {
            RequireCurrent(epoch, _progressCancellation);
            AdvanceOwnedEpoch(); RevokeFrame("map-preparation-unconfirmed");
            if (_capture.BindingScope is not null)
                _mapRecovery = new(CurrentRecoveryScope(), _options.TimeProvider.GetTimestamp());
            _reason = "map-open-preparation-deadline";
            _diagnostics = System.Text.Json.JsonSerializer.SerializeToElement(diagnostic, Json);
            Publish(); return Receipt(request, false, _reason);
        }
    }

    private async ValueTask<RuntimeOperationResult> RecoverMapOpen(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        RecoveryTicket row;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (_mapRecovery is not { } pending || Text(request.Payload, "recoveryId") != pending.Id)
                return Receipt(request, false, "cancelled-or-stale");
            var state = RecoveryStatus();
            if (!state.Pending)
            { _mapRecovery = null; Publish(); return Receipt(request, false, "map-open-recovery-exhausted"); }
            if (!state.Due) return Receipt(request, false, "map-open-recovery-waiting");
            row = pending; row.Remaining--;
            row.Next = _options.TimeProvider.GetElapsedTime(row.Started) + RecoveryInterval;
            _workActive = true; Publish();
        }
        // The shared capture/observation/solve consumer receives exactly one
        // probe, with this ticket's remaining lifetime and no new input sequence.
        return await CaptureAndSolve(request, epoch, token, row).ConfigureAwait(false);
    }
}
