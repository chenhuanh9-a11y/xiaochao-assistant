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
    RuntimeRenderPacket? applied;
    RuntimeRenderPacket? failed;
    double appliedOpacity;
    WpfAlignedOverlayTicket? ticket;
    Task? preparingMaterial;
    WpfRuntimeSceneMaterial? preparedMaterial;
    readonly List<WpfRuntimeSceneMaterial> readyMaterials=[];
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
        var packet = read();
        var candidate = readPendingMaterial() ?? readMaterialToRemember() ?? packet;
        if (candidate is not null && !ReferenceEquals(candidate, failed))
        {
            if (readyMaterials.FirstOrDefault(r=>r.Matches(candidate)) is { } ready)
            { preparedMaterial=ready;CompleteMaterialReady(candidate); }
            else if (preparingMaterial is not { IsCompleted: false }) preparingMaterial = PrepareMaterial(candidate);
        }
        if (packet is null) observeDisplayContext();
        if (packet is null || !Current(packet)) { Withdraw(); return; }
        if (ReferenceEquals(failed, packet)) return;
        double desiredOpacity = opacity();
        if (ReferenceEquals(applied, packet) && surface.IsVisible && appliedOpacity == desiredOpacity) return;
        if (readyMaterials.FirstOrDefault(r=>r.Matches(packet)) is not { } source)
        { Withdraw(); return; }
        bool sibling = applied is not null && applied.MapId == packet.MapId && applied.Stamp == packet.Stamp
            && applied.Client == packet.Client && surface.HasMaterial;
        ticket = sibling ? surface.Stage(packet) : surface.Reserve(packet);
        PrepareAndShow(packet, ticket, source, desiredOpacity);
    }

    // The visibility owner calls this synchronously on F11/UI changes; do not
    // leave an already visible HWND until the next timer tick.
    internal void RefreshVisibility() { dispatcher.VerifyAccess(); Pulse(); }

    async Task PrepareMaterial(RuntimeRenderPacket packet)
    {
        try
        {
            var ready = await Task.Run(() => WpfRuntimeSceneMaterial.Prepare(packet));
            dispatcher.VerifyAccess();
            if (disposed) return;
            var current = readPendingMaterial() ?? readMaterialToRemember() ?? read();
            if (current is not null && ready.Matches(current))
            {
                readyMaterials.RemoveAll(r=>ReferenceEquals(r.Owner,ready.Owner));readyMaterials.Insert(0,ready);
                if(readyMaterials.Count>2)readyMaterials.RemoveAt(2);
                preparedMaterial=ready;CompleteMaterialReady(current);
                if(ready.Unavailable.Count>0)report("部分楼层素材暂不可用，当前楼层继续显示。");
            }
        }
        catch (Exception error)
        {
            if (!disposed && readPendingMaterial() is { } pending
                && ReferenceEquals(WpfRuntimeSceneMaterial.OwnerOf(pending),WpfRuntimeSceneMaterial.OwnerOf(packet)))
            {
                rejectMaterial(pending, "wpf-material-error:" + error.GetType().Name);
                report("所选地图素材暂不可用，保留当前选择：" + error.Message);
            }
            else if (!disposed && (readMaterialToRemember() ?? read()) is { } current
                && ReferenceEquals(WpfRuntimeSceneMaterial.OwnerOf(current),WpfRuntimeSceneMaterial.OwnerOf(packet)))
            {
                failed = current; Withdraw();
                report("地图素材暂不可用：" + error.Message);
            }
        }
    }

    void CompleteMaterialReady(RuntimeRenderPacket offered)
    {
        if (ReferenceEquals(readPendingMaterial(), offered))
        {
            if (acceptMaterialReady(offered)) rememberMaterialReady(offered);
        }
        else if (readMaterialToRemember() is { } current && current.SharesMaterial(offered))
            rememberMaterialReady(offered);
    }

    void PrepareAndShow(RuntimeRenderPacket packet, WpfAlignedOverlayTicket ownTicket, WpfRuntimeSceneMaterial source, double desiredOpacity)
    {
        try
        {
            var prepared = source.Frame(ownTicket,packet,desiredOpacity);
            dispatcher.VerifyAccess();
            if (!Current(packet) || !ReferenceEquals(ticket, ownTicket) || opacity() != desiredOpacity) return;
            if (!surface.TryApply(ownTicket, prepared)) return;
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
        ticket = null; applied = null;
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
        Withdraw(); surface.Dispose(); preparedMaterial = null; readyMaterials.Clear();failed=null;
    }
}
