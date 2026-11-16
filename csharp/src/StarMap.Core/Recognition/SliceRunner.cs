namespace StarMap.Core.Recognition;

using System.Diagnostics;
using StarMap.Core.Frames;
using StarMap.Core.Protocol;
using StarMap.Core.Reliability;
using StarMap.Core.Session;

public sealed record SliceExecution(
    HandshakeReply Handshake,
    RequestTicket Ticket,
    GridSliceResult Result,
    ResultDisposition Disposition,
    double ComputeMilliseconds);

/// <summary>
/// One vertical slice: handshake, issue a sequenced request, evaluate the
/// recorded frame, and submit through the result gate. The runner does not
/// write session sequence numbers itself.
/// </summary>
public static class SliceRunner
{
    public static SliceExecution Run(
        RecordedFrame frame,
        SessionSequencer session,
        ResultGate gate,
        bool cancelImmediately = false,
        DateTimeOffset? now = null)
    {
        var reply = ProtocolHandshake.Exchange(new HandshakeHello(
            ProtocolHandshake.CurrentVersion,
            ProtocolHandshake.ComponentId,
            ProtocolHandshake.GridSliceCapability));
        if (!reply.Accepted)
            throw new InvalidOperationException(reply.Reason);
        var ticket = session.Issue();
        Func<bool>? cancelled = cancelImmediately ? () => true : null;
        var clock = Stopwatch.StartNew();
        var result = GridSlice.Evaluate(
            frame.Edge,
            frame.EdgeHeight,
            frame.EdgeWidth,
            frame.Distance,
            frame.PreparedHeight,
            frame.PreparedWidth,
            frame.Scale,
            frame.PriorX,
            frame.PriorY,
            cancelled);
        clock.Stop();
        var disposition = gate.Submit(ticket, ticket.Sequence, now ?? DateTimeOffset.UtcNow);
        return new SliceExecution(reply, ticket, result, disposition, clock.Elapsed.TotalMilliseconds);
    }
}
