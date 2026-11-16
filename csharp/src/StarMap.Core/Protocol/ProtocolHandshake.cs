namespace StarMap.Core.Protocol;

/// <summary>
/// Phase-1 capability handshake. A version or capability mismatch is a hard
/// refusal. This stub does not invent resources or skip validation.
/// </summary>
public static class ProtocolHandshake
{
    public const int CurrentVersion = 1;
    public const string ComponentId = "starmap-recognition-slice";
    public const string GridSliceCapability = "grid-slice-v1";

    public static HandshakeReply Exchange(HandshakeHello peer)
    {
        if (peer.ProtocolVersion != CurrentVersion)
            return HandshakeReply.Reject("protocol-version-mismatch");
        if (string.IsNullOrWhiteSpace(peer.ComponentId))
            return HandshakeReply.Reject("component-missing");
        if (!string.IsNullOrEmpty(peer.RequiredCapability)
            && peer.RequiredCapability != GridSliceCapability)
            return HandshakeReply.Reject("capability-missing");
        return HandshakeReply.Accept(CurrentVersion, ComponentId, GridSliceCapability);
    }
}

public readonly record struct HandshakeHello(
    int ProtocolVersion,
    string ComponentId,
    string? RequiredCapability);

public readonly record struct HandshakeReply(
    bool Accepted,
    string Reason,
    int ProtocolVersion,
    string ComponentId,
    string Capability)
{
    public static HandshakeReply Accept(int version, string componentId, string capability)
        => new(true, "accepted", version, componentId, capability);

    public static HandshakeReply Reject(string reason)
        => new(false, reason, 0, "", "");
}
