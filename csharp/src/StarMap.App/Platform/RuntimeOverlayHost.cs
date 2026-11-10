using System.IO;
using System.Windows.Threading;
using StarMap.Runtime;

namespace StarMap.App.Platform;

/// <summary>Always-on consumer; its lifetime does not depend on the selected workspace page.</summary>
internal sealed class RuntimeOverlayHost : IDisposable
{
    readonly Dispatcher dispatcher;
    readonly Func<RuntimeRenderPacket?> read;
    readonly WindowsRuntimeCapturePort capture;
    readonly Action<string> report;
    readonly Func<double> opacity;
    readonly Func<bool> visible;
    readonly Func<RuntimeRenderPacket?> readPendingMaterial;
    readonly Func<RuntimeRenderPacket, bool> acceptMaterialReady;
    readonly Action<RuntimeRenderPacket, string> rejectMaterial;
    readonly Func<RuntimeRenderPacket?> readMaterialToRemember;
    readonly Func<RuntimeRenderPacket, bool> rememberMaterialReady;
    readonly Action observeDisplayContext;
    readonly WpfAlignedOverlaySurface surface;
    readonly DispatcherTimer timer;
    RuntimeRenderPacket? requested, applied;
    RuntimeRenderPacket? failed;
    double appliedOpacity;
    WpfAlignedOverlayTicket? ticket;
    Task? preparing;
    Task? preparingGroupMaterial;
    RuntimeRenderPacket? rememberedMaterial, failedMaterial;
    bool disposed;
    int captureLeases;

    internal RuntimeOverlayHost(Dispatcher dispatcher, Func<RuntimeRenderPacket?> read,
        WindowsRuntimeCapturePort capture, Action<string> report, Func<double>? opacity = null, Func<bool>? visible = null,
        Func<RuntimeRenderPacket?>? readPendingMaterial = null, Func<RuntimeRenderPacket, bool>? acceptMaterialReady = null,
        Action<RuntimeRenderPacket, string>? rejectMaterial = null,
        Func<RuntimeRenderPacket?>? readMaterialToRemember = null, Func<RuntimeRenderPacket, bool>? rememberMaterialReady = null,
        Action? observeDisplayContext = null)
    {
        this.dispatcher = dispatcher; this.read = read; this.capture = capture; this.report = report;
        this.opacity = opacity ?? (() => .55);
        this.visible = visible ?? (() => true);
        this.readPendingMaterial = readPendingMaterial ?? (() => null);
        this.acceptMaterialReady = acceptMaterialReady ?? (_ => false);
        this.rejectMaterial = rejectMaterial ?? ((_, _) => { });
        this.readMaterialToRemember = readMaterialToRemember ?? (() => null);
        this.rememberMaterialReady = rememberMaterialReady ?? (_ => false);
        this.observeDisplayContext = observeDisplayContext ?? (() => { });
        surface = new(dispatcher);
        capture.AcquireOverlayCaptureLease = SuspendForCapture;
        timer = new(TimeSpan.FromMilliseconds(50), DispatcherPriority.Background, (_, _) => Pulse(), dispatcher);
        timer.Start();
    }

    bool Current(RuntimeRenderPacket packet) => !disposed && visible() && captureLeases == 0 && packet.IsLive
        && ReferenceEquals(read(), packet) && capture.IsForegroundObservationCurrent(packet.Stamp, packet.Client);

    void Pulse()
    {
        if (disposed) return;
        // Hidden and Recorded material still reaches the real WPF consumer.
        // Its readiness cannot authorize native display or bypass Session receipt collection.
        var candidate = readPendingMaterial() ?? readMaterialToRemember();
        if (preparingGroupMaterial is not { IsCompleted: false } && candidate is not null
            && !ReferenceEquals(candidate, rememberedMaterial) && !ReferenceEquals(candidate, failedMaterial))
            preparingGroupMaterial = PrepareGroupMaterial(candidate);
        var packet = read();
        if (packet is null) observeDisplayContext();
        if (packet is null || !Current(packet)) { Withdraw(); return; }
        if (ReferenceEquals(failed, packet)) return;
        double desiredOpacity = opacity();
        if (ReferenceEquals(applied, packet) && surface.IsVisible && appliedOpacity == desiredOpacity) return;
        if (preparing is { IsCompleted: false })
        {
            if (!ReferenceEquals(requested, packet)) Withdraw();
            return;
        }
        requested = packet;
        bool sibling = applied is not null && applied.MapId == packet.MapId && applied.Stamp == packet.Stamp
            && applied.Client == packet.Client && surface.HasMaterial;
        ticket = sibling ? surface.Stage(packet) : surface.Reserve(packet);
        preparing = PrepareAndShow(packet, ticket, desiredOpacity);
    }

    // The visibility owner calls this synchronously on F11/UI changes; do not
    // leave an already visible HWND until the next timer tick.
    internal void RefreshVisibility() { dispatcher.VerifyAccess(); Pulse(); }

    async Task PrepareGroupMaterial(RuntimeRenderPacket packet)
    {
        try
        {
            var ready = await Task.Run(() => WpfDisplayMaterialConsumer.PrepareMaterialReady(packet.Material));
            dispatcher.VerifyAccess();
            if (disposed || !ready.IsFrozen) return;
            if (ReferenceEquals(readPendingMaterial(), packet))
            {
                if (!acceptMaterialReady(packet)) return;
                if (rememberMaterialReady(packet)) rememberedMaterial = packet;
                Pulse();
            }
            else if (ReferenceEquals(readMaterialToRemember(), packet) && rememberMaterialReady(packet))
                rememberedMaterial = packet;
        }
        catch (Exception error)
        {
            if (!disposed && ReferenceEquals(readPendingMaterial(), packet))
            {
                rejectMaterial(packet, "wpf-material-error:" + error.GetType().Name);
                report("所选地图素材暂不可用，保留当前选择：" + error.Message);
            }
            else if (!disposed && ReferenceEquals(readMaterialToRemember(), packet))
            {
                failedMaterial = failed = packet; Withdraw();
                report("地图素材暂不可用：" + error.Message);
            }
        }
    }

    async Task PrepareAndShow(RuntimeRenderPacket packet, WpfAlignedOverlayTicket ownTicket, double desiredOpacity)
    {
        try
        {
            var geometry = packet.Client!.Geometry;
            var affine = packet.Material.Bridge.DisplayToClient ?? throw new InvalidDataException("overlay-display-transform-unavailable");
            var projected = await Task.Run(() => WpfAlignedOverlaySurface.Prepare(ownTicket,
                new(packet.Material.Width, packet.Material.Height, packet.Material.Stride, packet.Material.CopyPixels()),
                new(affine.M00, affine.M01, affine.M02, affine.M10, affine.M11, affine.M12, 0, 0, 1),
                new(geometry.Left, geometry.Top, geometry.Width, geometry.Height, geometry.Dpi), desiredOpacity));
            dispatcher.VerifyAccess();
            if (!Current(packet) || !ReferenceEquals(ticket, ownTicket) || opacity() != desiredOpacity) return;
            if (!surface.TryApply(ownTicket, projected)) return;
            if (!Current(packet) || !surface.ShowCurrent(ownTicket)) { Withdraw(); return; }
            // Showing a passive HWND must not have changed the foreground owner.
            if (!Current(packet)) { Withdraw(); return; }
            applied = packet; appliedOpacity = desiredOpacity;
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!disposed && ReferenceEquals(ticket, ownTicket))
            { Withdraw(); failed = packet; report("悬浮地图暂不可用：" + error.Message); }
        }
    }

    void Withdraw()
    {
        if (ticket is not null || applied is not null || surface.HasMaterial || surface.IsVisible) surface.Withdraw();
        ticket = null; requested = null; applied = null;
    }

    async ValueTask<IAsyncDisposable> SuspendForCapture(CancellationToken cancellation)
    {
        await dispatcher.InvokeAsync(() =>
        {
            cancellation.ThrowIfCancellationRequested();
            ObjectDisposedException.ThrowIf(disposed, this);
            captureLeases++; Withdraw();
        });
        return new CaptureLease(this);
    }

    sealed class CaptureLease(RuntimeOverlayHost owner) : IAsyncDisposable
    {
        int released;
        public async ValueTask DisposeAsync()
        {
            if (Interlocked.Exchange(ref released, 1) != 0) return;
            await owner.dispatcher.InvokeAsync(() => { owner.captureLeases--; });
        }
    }

    public void Dispose()
    {
        dispatcher.VerifyAccess();
        if (disposed) return;
        disposed = true; timer.Stop(); capture.AcquireOverlayCaptureLease = null;
        Withdraw(); surface.Dispose();
    }
}
