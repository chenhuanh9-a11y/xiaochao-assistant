namespace StarMap.Core.Reliability;

using StarMap.Core.Session;

public enum ResultDisposition
{
    Accepted,
    StaleSequence,
    FutureSequence,
    Duplicate,
    SessionMismatch,
    GenerationMismatch,
    Cancelled,
    Expired,
    SessionClosed,
}

/// <summary>
/// Accepts a worker result only when it still belongs to the current session
/// sequence. Disposition bookkeeping lives here; the sequence counter does not.
/// Timeout marks the request expired and only then cancels it.
/// </summary>
public sealed class ResultGate
{
    private readonly SessionSequencer _session;
    private readonly HashSet<long> _cancelled = new();
    private readonly HashSet<long> _expired = new();
    private readonly HashSet<long> _accepted = new();

    public ResultGate(SessionSequencer session)
    {
        _session = session ?? throw new ArgumentNullException(nameof(session));
    }

    public event Action<RequestTicket>? ExpiredObserved;
    public event Action<RequestTicket>? CancelObserved;

    public void ExpireThenCancel(RequestTicket ticket)
    {
        PruneSuperseded();
        _expired.Add(ticket.Sequence);
        ExpiredObserved?.Invoke(ticket);
        _cancelled.Add(ticket.Sequence);
        CancelObserved?.Invoke(ticket);
    }

    public void Cancel(RequestTicket ticket)
    {
        PruneSuperseded();
        _cancelled.Add(ticket.Sequence);
        CancelObserved?.Invoke(ticket);
    }

    public ResultDisposition Submit(RequestTicket ticket, long resultSequence, DateTimeOffset now)
    {
        PruneSuperseded();
        var snapshot = _session.Read();
        if (snapshot.Closed)
            return ResultDisposition.SessionClosed;
        if (!string.Equals(ticket.SessionId, snapshot.SessionId, StringComparison.Ordinal))
            return ResultDisposition.SessionMismatch;
        if (resultSequence != ticket.Sequence)
            return resultSequence < ticket.Sequence
                ? ResultDisposition.StaleSequence
                : ResultDisposition.FutureSequence;
        if (ticket.Generation != snapshot.Generation)
            return ResultDisposition.GenerationMismatch;
        if (ticket.Sequence <= snapshot.SupersededThrough)
            return ResultDisposition.StaleSequence;
        if (ticket.Sequence > snapshot.LastIssuedSequence)
            return ResultDisposition.FutureSequence;
        if (ticket.DeadlineUtc is DateTimeOffset deadline && now >= deadline)
            return ResultDisposition.Expired;
        if (_expired.Contains(ticket.Sequence))
            return ResultDisposition.Expired;
        if (_cancelled.Contains(ticket.Sequence))
            return ResultDisposition.Cancelled;
        if (!_accepted.Add(ticket.Sequence))
            return ResultDisposition.Duplicate;
        return ResultDisposition.Accepted;
    }

    private void PruneSuperseded()
    {
        // Old sequences already fail the sequencer checks; retaining every
        // terminal request would grow for the entire application lifetime.
        var through = _session.Read().SupersededThrough;
        _cancelled.RemoveWhere(sequence => sequence <= through);
        _expired.RemoveWhere(sequence => sequence <= through);
        _accepted.RemoveWhere(sequence => sequence <= through);
    }
}
