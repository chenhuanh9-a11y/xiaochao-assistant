using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace StarMap.App.Platform;

/// <summary>Local presentation ticket, not a runtime/identity authorization. OwnerToken is opaque and compared by reference.</summary>
public sealed class WpfAlignedOverlayTicket
{
    readonly CancellationTokenSource preparation = new();
    internal WpfAlignedOverlayTicket(object surface, object ownerToken, long revision)
    { Surface = surface; OwnerToken = ownerToken; Revision = revision; }
    internal object Surface { get; }
    public object OwnerToken { get; }
    public long Revision { get; }
    internal CancellationToken PreparationCancellation => preparation.Token;
    internal void Revoke() => preparation.Cancel();
}

public sealed class WpfAlignedOverlayPreparedFrame
{
    internal WpfAlignedOverlayPreparedFrame(WpfAlignedOverlayTicket ticket, WpfAlignedOverlayPreparedFrame pixels, double opacity)
    {
        Ticket = ticket; ClientPixels = pixels.ClientPixels; Client = pixels.Client;
        DisplayToClient = pixels.DisplayToClient; Opacity = opacity; Image = pixels.Image;
    }
    internal WpfAlignedOverlayPreparedFrame(WpfAlignedOverlayTicket ticket, WpfOverlayBgra32 clientPixels,
        WpfOverlayClientGeometry client, WpfOverlayHomography displayToClient, double opacity)
    {
        Ticket = ticket; ClientPixels = clientPixels; Client = client; DisplayToClient = displayToClient; Opacity = opacity;
        Image = BitmapSource.Create(client.Width, client.Height, 96, 96, PixelFormats.Bgra32, null,
            clientPixels.CopyPixels(), clientPixels.Stride);
        Image.Freeze();
    }
    public WpfAlignedOverlayTicket Ticket { get; }
    public WpfOverlayBgra32 ClientPixels { get; }
    public WpfOverlayClientGeometry Client { get; }
    public WpfOverlayHomography DisplayToClient { get; }
    public double Opacity { get; }
    public BitmapSource Image { get; }
}

/// <summary>
/// Read-only WPF presentation surface. Constructor, Reserve, Prepare and TryApply never show or activate it.
/// The caller must revalidate its current runtime grant/foreground immediately before TryApply and ShowCurrent,
/// and Withdraw on revocation, map close, foreground loss, binding/resource/floor/session change or shutdown.
/// No capture, recognition, image loading, keyboard hook, focus-return policy or business authority lives here.
/// </summary>
public sealed class WpfAlignedOverlaySurface : IDisposable
{
    readonly Dispatcher dispatcher;
    readonly object identity = new();
    readonly object stateGate = new();
    readonly WpfAlignedOverlayVisual viewport;
    readonly IWpfAlignedOverlayWindow window;
    WpfAlignedOverlayTicket? current, pending, placementTicket;
    WpfAlignedOverlayPreparedFrame? applied;
    WpfOverlayClientGeometry? placementClient;
    long revision, stateVersion;
    bool disposed, visibleRequested;

    public WpfAlignedOverlaySurface() : this(Dispatcher.CurrentDispatcher) { }
    public WpfAlignedOverlaySurface(Dispatcher dispatcher) : this(dispatcher, new WpfAlignedOverlayWindow()) { }
    internal WpfAlignedOverlaySurface(Dispatcher dispatcher, IWpfAlignedOverlayWindow window)
    {
        this.dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher)); dispatcher.VerifyAccess();
        this.window = window ?? throw new ArgumentNullException(nameof(window)); viewport = new WpfAlignedOverlayVisual();
    }

    /// <summary>Owned HWND, or zero before first TryApply. Reading it never creates or shows a window.</summary>
    public nint NativeHandle { get { dispatcher.VerifyAccess(); return window.Handle; } }
    public bool HasMaterial { get { dispatcher.VerifyAccess(); return applied is not null && viewport.HasMaterial; } }
    public bool IsVisible { get { dispatcher.VerifyAccess(); return window.IsVisible; } }
    public WpfOverlayCaptureExclusion CaptureExclusion => window.CaptureExclusion;
    public uint ActualDpi { get; private set; }
    internal FrameworkElement Visual { get { dispatcher.VerifyAccess(); return viewport; } }

    /// <summary>Starts one caller-supplied owner request and clears the previous material. Does not create a HWND.</summary>
    public WpfAlignedOverlayTicket Reserve(object opaqueOwnerToken)
    {
        dispatcher.VerifyAccess(); ArgumentNullException.ThrowIfNull(opaqueOwnerToken);
        lock (stateGate) ObjectDisposedException.ThrowIf(disposed, this);
        Withdraw();
        lock (stateGate)
        {
            ObjectDisposedException.ThrowIf(disposed, this); stateVersion++;
            return current = new WpfAlignedOverlayTicket(identity, opaqueOwnerToken, ++revision);
        }
    }

    /// <summary>
    /// Replaces the single pending preparation only; an applied current material remains until successful TryApply.
    /// Sibling material must keep the complete applied ClientGeometry. Geometry/context changes require Withdraw/Reserve.
    /// </summary>
    public WpfAlignedOverlayTicket Stage(object opaqueOwnerToken)
    {
        dispatcher.VerifyAccess(); ArgumentNullException.ThrowIfNull(opaqueOwnerToken);
        WpfAlignedOverlayTicket? previous; WpfAlignedOverlayTicket ticket; bool clearRevokedMaterial;
        lock (stateGate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            previous = pending; clearRevokedMaterial = current is null;
            pending = ticket = new WpfAlignedOverlayTicket(identity, opaqueOwnerToken, ++revision); stateVersion++;
        }
        previous?.Revoke();
        if (clearRevokedMaterial) ClearVisual();
        return ticket;
    }

    /// <summary>May run on a worker. The immutable result cannot show itself; late results fail TryApply.</summary>
    public static WpfAlignedOverlayPreparedFrame Prepare(WpfAlignedOverlayTicket ticket, WpfOverlayBgra32 display,
        WpfOverlayHomography displayToClient, WpfOverlayClientGeometry client, double opacity = 1,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(ticket);
        if (!double.IsFinite(opacity) || opacity is < 0 or > 1) throw new ArgumentOutOfRangeException(nameof(opacity));
        using var preparation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, ticket.PreparationCancellation);
        var pixels = WpfAlignedOverlayRasterizer.Project(display, displayToClient, client, preparation.Token);
        preparation.Token.ThrowIfCancellationRequested();
        return new(ticket, pixels, client, displayToClient, opacity);
    }

    /// <summary>Reuses immutable projected pixels with a NEW presentation ticket. Never renews runtime authority.</summary>
    internal static WpfAlignedOverlayPreparedFrame ReuseProjection(WpfAlignedOverlayTicket ticket,
        WpfAlignedOverlayPreparedFrame previous, double opacity)
    {
        ArgumentNullException.ThrowIfNull(ticket); ArgumentNullException.ThrowIfNull(previous);
        if (!double.IsFinite(opacity) || opacity is < 0 or > 1) throw new ArgumentOutOfRangeException(nameof(opacity));
        ticket.PreparationCancellation.ThrowIfCancellationRequested();
        return new(ticket, previous, opacity);
    }

    public bool IsCurrent(WpfAlignedOverlayTicket ticket)
    { lock (stateGate) return !disposed && ReferenceEquals(ticket.Surface, identity) && ReferenceEquals(current, ticket); }
    public bool IsPending(WpfAlignedOverlayTicket ticket)
    { lock (stateGate) return !disposed && ReferenceEquals(ticket.Surface, identity) && ReferenceEquals(pending, ticket); }
    bool IsAdmissible(WpfAlignedOverlayTicket ticket) => IsCurrent(ticket) || IsPending(ticket);

    /// <summary>
    /// Commits material without a visibility change. It cannot make an invisible surface visible.
    /// Once a frame is applied, a different complete ClientGeometry is rejected before any native Ensure/Place;
    /// only a mismatching pending ticket is canceled, while the current owner/material/position remain intact.
    /// Use Withdraw/Reserve to authorize a new geometry before preparing its first material.
    /// </summary>
    public bool TryApply(WpfAlignedOverlayTicket ticket, WpfAlignedOverlayPreparedFrame prepared)
    {
        dispatcher.VerifyAccess(); ArgumentNullException.ThrowIfNull(ticket); ArgumentNullException.ThrowIfNull(prepared);
        if (!IsAdmissible(ticket) || !ReferenceEquals(prepared.Ticket, ticket)) return false;
        var previousFrame = applied;
        if (previousFrame is not null && prepared.Client != previousFrame.Client)
        {
            CancelPending(ticket);
            return false;
        }
        try
        {
            placementClient = prepared.Client; placementTicket = ticket;
            window.Ensure(prepared.Client, viewport, WindowMessage, DpiChanged);
            if (!IsAdmissible(ticket)) return false;
            window.Place(prepared.Client, false);
            if (!IsAdmissible(ticket)) return false;
            var dpi = ReadDeviceDpi();
            WpfAlignedOverlayTicket? replaced;
            lock (stateGate)
            {
                if (disposed || !(ReferenceEquals(current, ticket) || ReferenceEquals(pending, ticket))) return false;
                // UI visual commit and local owner switch share one lock/dispatcher transaction.
                viewport.Apply(prepared, dpi.PixelsPerInchX, dpi.PixelsPerInchY);
                replaced = ReferenceEquals(current, ticket) ? null : current;
                current = ticket; if (ReferenceEquals(pending, ticket)) pending = null;
                applied = prepared; stateVersion++;
            }
            replaced?.Revoke();
            if (!IsCurrent(ticket)) { ClearVisual(); return false; }
            return true;
        }
        catch (Exception failure)
        {
            // A failed staged sibling must not withdraw a still-current predecessor.
            if (!IsAdmissible(ticket)) return false;
            if (IsPending(ticket) && previousFrame is not null && IsCurrent(previousFrame.Ticket))
            {
                CancelPending(ticket);
                try
                {
                    placementClient = previousFrame.Client; placementTicket = previousFrame.Ticket;
                    window.Place(previousFrame.Client, false);
                    var dpi = ReadDeviceDpi(); viewport.Apply(previousFrame, dpi.PixelsPerInchX, dpi.PixelsPerInchY);
                }
                catch (Exception restoreFailure)
                {
                    var restorationFailure = new AggregateException("Staged material and restoration both failed.", failure, restoreFailure);
                    WithdrawAfterFailure(restorationFailure); throw restorationFailure;
                }
            }
            else WithdrawAfterFailure(failure);
            throw;
        }
    }

    /// <summary>The only explicit visible action. Native SWP_NOACTIVATE and passive HWND styles preserve foreground.</summary>
    public bool ShowCurrent(WpfAlignedOverlayTicket ticket)
    {
        dispatcher.VerifyAccess(); ArgumentNullException.ThrowIfNull(ticket);
        if (!IsCurrent(ticket) || applied is null || !ReferenceEquals(applied.Ticket, ticket) || !window.IsCreated) return false;
        try
        {
            window.ValidatePassive(); var dpi = ReadDeviceDpi();
            viewport.Apply(applied, dpi.PixelsPerInchX, dpi.PixelsPerInchY);
            if (!IsCurrent(ticket)) return false;
            lock (stateGate)
            {
                if (disposed || !ReferenceEquals(current, ticket)) return false;
                visibleRequested = true;
            }
            placementClient = applied.Client; placementTicket = ticket;
            window.Place(applied.Client, true);
            if (!IsCurrent(ticket)) { ClearVisual(); return false; }
            return true;
        }
        catch (Exception failure)
        {
            WithdrawAfterFailure(failure);
            throw;
        }
    }

    /// <summary>Revokes tickets immediately, then synchronously hides and releases material on the UI dispatcher.</summary>
    public void Withdraw()
    {
        WpfAlignedOverlayTicket? oldCurrent, oldPending; long revokedVersion;
        lock (stateGate)
        {
            oldCurrent = current; oldPending = pending; current = null; pending = null; visibleRequested = false;
            revokedVersion = ++stateVersion;
        }
        oldCurrent?.Revoke(); oldPending?.Revoke();
        void ClearRevokedVisual()
        {
            // A newer explicit request/commit outranks an older worker's dispatched cleanup.
            lock (stateGate) { if (stateVersion != revokedVersion) return; }
            ClearVisual();
        }
        if (dispatcher.CheckAccess()) ClearRevokedVisual();
        else dispatcher.Invoke(ClearRevokedVisual, DispatcherPriority.Send);
    }

    void ClearVisual()
    {
        lock (stateGate) visibleRequested = false;
        applied = null; placementClient = null; placementTicket = null; viewport.ClearMaterial(); window.Hide();
    }

    void CancelPending(WpfAlignedOverlayTicket ticket)
    {
        lock (stateGate)
        {
            if (!ReferenceEquals(pending, ticket)) return;
            pending = null; stateVersion++;
        }
        ticket.Revoke();
    }

    void WithdrawAfterFailure(Exception failure)
    {
        try { Withdraw(); }
        catch (Exception withdrawalFailure) { throw new AggregateException("Overlay presentation and withdrawal both failed.", failure, withdrawalFailure); }
    }

    DpiScale ReadDeviceDpi()
    {
        ActualDpi = window.NativeDpi; var device = window.DeviceDpi;
        if (ActualDpi == 0 || Math.Abs(device.PixelsPerInchX - ActualDpi) > .01 || Math.Abs(device.PixelsPerInchY - ActualDpi) > .01)
            throw new InvalidOperationException("The overlay composition-target DPI differs from its own native HWND DPI.");
        // Capture/game DPI is request context only. It need not equal a spanning overlay's selected monitor DPI.
        return device;
    }

    void DpiChanged(object sender, HwndDpiChangedEventArgs args)
    {
        // WPF 10's unhandled path calls SuggestedRect SetWindowPos without NOACTIVATE. Stop that path and retire
        // this composition target instead of reusing its stale device DPI. A fresh explicit request creates a new source.
        args.Handled = true;
        try { Withdraw(); }
        finally { window.Retire(); ActualDpi = 0; }
    }

    nint WindowMessage(nint hwnd, int message, nint wParam, nint lParam, ref bool handled)
    {
        switch (message)
        {
            case 0x0046: // WM_WINDOWPOSCHANGING; keep WPF's normal layout/target message processing.
                if (lParam != 0)
                {
                    WpfOverlayClientGeometry? rectangle; bool allowVisible;
                    lock (stateGate)
                    {
                        bool validPlacement = !disposed && placementTicket is not null
                            && (ReferenceEquals(current, placementTicket) || ReferenceEquals(pending, placementTicket));
                        rectangle = validPlacement ? placementClient : null;
                        allowVisible = !disposed && visibleRequested && current is not null && applied is not null && ReferenceEquals(current, applied.Ticket);
                    }
                    var position = Marshal.PtrToStructure<WpfAlignedOverlayNative.WindowPosition>(lParam);
                    position = WpfAlignedOverlayNative.ConstrainPosition(position, rectangle, allowVisible);
                    Marshal.StructureToPtr(position, lParam, false);
                }
                break;
            case 0x0084: handled = true; return (nint)(-1); // WM_NCHITTEST → HTTRANSPARENT.
            case 0x0021: handled = true; return (nint)3; // WM_MOUSEACTIVATE → MA_NOACTIVATE; no input hook/suppression.
            case 0x0010: handled = true; Withdraw(); break; // WM_CLOSE revokes; old prepared work stays stale.
        }
        return 0;
    }

    public void Dispose()
    {
        dispatcher.VerifyAccess(); lock (stateGate) { if (disposed) return; disposed = true; }
        try { Withdraw(); }
        finally
        {
            window.Dispose();
        }
    }
}

/// <summary>The same pixel→DIP viewport is used by the native surface and offscreen WPF verification.</summary>
internal sealed class WpfAlignedOverlayVisual : Grid
{
    readonly Image image = new() { Stretch = Stretch.Fill, IsHitTestVisible = false, Focusable = false, SnapsToDevicePixels = true };
    internal WpfAlignedOverlayVisual()
    {
        Background = Brushes.Transparent; ClipToBounds = true; IsHitTestVisible = false; Focusable = false;
        RenderOptions.SetBitmapScalingMode(image, BitmapScalingMode.NearestNeighbor); Children.Add(image);
    }
    internal bool HasMaterial => image.Source is not null;
    internal void Apply(WpfAlignedOverlayPreparedFrame prepared, double actualDpiX, double actualDpiY)
    {
        var size = prepared.Client.DipSize(actualDpiX, actualDpiY);
        image.Source = prepared.Image; image.Opacity = prepared.Opacity;
        Width = size.Width; Height = size.Height;
        Measure(new Size(size.Width, size.Height)); Arrange(new Rect(0, 0, size.Width, size.Height)); UpdateLayout();
    }
    internal void ClearMaterial() { image.Source = null; image.Opacity = 0; Width = 0; Height = 0; }
}
