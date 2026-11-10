using System.ComponentModel;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Text;

namespace StarMap.Windows;

/// <summary>Read-only HWND inspection and explicit GDI client-pixel acquisition.</summary>
public static class WindowCapture
{
    private const long MaxPixels = 32_000_000;
    private const int MaxWindowTitleLength = 32_768;
    private const int MaxProcessPathLength = 32_768;
    private const string IdentityVSeparators = " -–—|:·[()";

    /// <summary>Reads native facts for the supplied HWND without finding or changing windows.</summary>
    public static WindowSnapshot Inspect(nint hwnd)
    {
        ValidateHwnd(hwnd);
        var firstIdentity = ReadIdentity(hwnd);
        var title = ReadTitle(hwnd);
        var visible = NativeMethods.IsWindowVisible(hwnd);
        var minimized = NativeMethods.IsIconic(hwnd);
        var geometry = ReadGeometry(hwnd);
        var foreground = NativeMethods.GetForegroundWindow();
        var secondIdentity = ReadIdentity(hwnd);
        var secondGeometry = ReadGeometry(hwnd);

        if (!SameIdentity(firstIdentity, secondIdentity) || geometry != secondGeometry)
        {
            throw new InvalidOperationException("Window identity or client geometry changed during inspection.");
        }

        return new WindowSnapshot(firstIdentity, title, visible, minimized, geometry, foreground);
    }

    /// <summary>Applies the existing strict Identity V title and executable policy.</summary>
    public static bool IsIdentityV(WindowSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        var executable = Path.GetFileName(snapshot.Identity.ExecutablePath);
        return string.Equals(executable, "dwrg.exe", StringComparison.OrdinalIgnoreCase)
            && IsIdentityVTitle(snapshot.Title);
    }

    /// <summary>Captures the complete client area using GetDC(hwnd) and SRCCOPY.</summary>
    public static CapturedClientFrame CaptureClient(WindowSnapshot snapshot, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        return CaptureRegion(
            snapshot,
            new ClientRect(0, 0, snapshot.Geometry.Width, snapshot.Geometry.Height),
            cancellationToken);
    }

    /// <summary>Captures a half-open client rectangle using GetDC(hwnd) and SRCCOPY.</summary>
    public static CapturedClientFrame CaptureRegion(
        WindowSnapshot snapshot,
        ClientRect requestedClientBounds,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        var clientBounds = ClipToClient(requestedClientBounds, snapshot.Geometry);
        return CaptureCore(
            snapshot,
            requestedClientBounds,
            clientBounds,
            useScreenDc: false,
            cancellationToken);
    }

    /// <summary>
    /// Explicitly captures the full client rectangle from the desktop DC at the snapshot's
    /// physical screen origin. Call only after the owner has verified its display-affinity
    /// exclusion policy; this method does not apply that policy or use any fallback.
    /// </summary>
    public static CapturedClientFrame CaptureScreenClient(
        WindowSnapshot snapshot,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        var bounds = new ClientRect(0, 0, snapshot.Geometry.Width, snapshot.Geometry.Height);
        return CaptureCore(snapshot, bounds, bounds, useScreenDc: true, cancellationToken);
    }

    private static CapturedClientFrame CaptureCore(
        WindowSnapshot snapshot,
        ClientRect requestedClientBounds,
        ClientRect clientBounds,
        bool useScreenDc,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var before = Inspect(snapshot.Identity.Hwnd);
        EnsureSameContext(snapshot, before);
        if (before.Minimized)
        {
            throw new InvalidOperationException("Cannot capture a minimized window.");
        }

        var width = clientBounds.Width;
        var height = clientBounds.Height;
        if (width <= 0 || height <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(clientBounds), "Capture bounds must have positive dimensions.");
        }

        var pixelCount = checked((long)width * height);
        if (pixelCount > MaxPixels)
        {
            throw new ArgumentOutOfRangeException(nameof(clientBounds), $"Capture exceeds the {MaxPixels:N0}-pixel limit.");
        }

        var stride = checked(width * 4);
        var byteCount = checked(stride * height);
        var imageBytes = checked((uint)byteCount);
        cancellationToken.ThrowIfCancellationRequested();

        var sourceOwner = useScreenDc ? nint.Zero : snapshot.Identity.Hwnd;
        var sourceDc = nint.Zero;
        var memoryDc = nint.Zero;
        var bitmap = nint.Zero;
        var previousObject = nint.Zero;
        var operationFailure = (Exception?)null;
        var cleanupFailures = new List<Exception>();
        CapturedClientFrame? result = null;

        try
        {
            sourceDc = NativeMethods.GetDC(sourceOwner);
            if (sourceDc == nint.Zero)
            {
                throw NativeFailure("GetDC");
            }

            memoryDc = NativeMethods.CreateCompatibleDC(sourceDc);
            if (memoryDc == nint.Zero)
            {
                throw NativeFailure("CreateCompatibleDC");
            }

            var bitmapInfo = new NativeMethods.BitmapInfo
            {
                Header = new NativeMethods.BitmapInfoHeader
                {
                    Size = checked((uint)Marshal.SizeOf<NativeMethods.BitmapInfoHeader>()),
                    Width = width,
                    Height = -height,
                    Planes = 1,
                    BitCount = 32,
                    Compression = NativeMethods.BiRgb,
                    SizeImage = imageBytes,
                },
            };
            bitmap = NativeMethods.CreateDIBSection(
                memoryDc,
                ref bitmapInfo,
                NativeMethods.DibRgbColors,
                out var bits,
                nint.Zero,
                0);
            if (bitmap == nint.Zero || bits == nint.Zero)
            {
                throw NativeFailure("CreateDIBSection");
            }

            previousObject = NativeMethods.SelectObject(memoryDc, bitmap);
            if (previousObject == nint.Zero || previousObject == NativeMethods.HgdiError)
            {
                previousObject = nint.Zero;
                throw NativeFailure("SelectObject");
            }

            cancellationToken.ThrowIfCancellationRequested();
            var sourceX = useScreenDc
                ? checked(before.Geometry.Left + clientBounds.Left)
                : clientBounds.Left;
            var sourceY = useScreenDc
                ? checked(before.Geometry.Top + clientBounds.Top)
                : clientBounds.Top;
            if (!NativeMethods.BitBlt(memoryDc, 0, 0, width, height, sourceDc, sourceX, sourceY, NativeMethods.Srccopy))
            {
                throw NativeFailure("BitBlt");
            }

            if (!NativeMethods.GdiFlush())
            {
                throw NativeFailure("GdiFlush");
            }

            cancellationToken.ThrowIfCancellationRequested();
            var bgra = new byte[byteCount];
            Marshal.Copy(bits, bgra, 0, byteCount);
            // GDI does not define the high byte for BI_RGB 32bpp DIBs.
            for (var alpha = 3; alpha < bgra.Length; alpha += 4)
            {
                bgra[alpha] = byte.MaxValue;
            }

            cancellationToken.ThrowIfCancellationRequested();
            var after = Inspect(snapshot.Identity.Hwnd);
            EnsureSameContext(snapshot, after);
            if (after.Minimized)
            {
                throw new InvalidOperationException("Window became minimized during capture.");
            }

            cancellationToken.ThrowIfCancellationRequested();
            var context = new ClientCaptureContext(before.Identity, before.Geometry);
            var acquisition = useScreenDc ? "screen-dc-client-bounds" : "client-dc-bitblt";
            result = new CapturedClientFrame(
                width,
                height,
                stride,
                bgra,
                context,
                requestedClientBounds,
                clientBounds,
                acquisition);
        }
        catch (Exception error)
        {
            operationFailure = error;
        }

        if (previousObject != nint.Zero)
        {
            TryCleanup(cleanupFailures, () =>
            {
                var replaced = NativeMethods.SelectObject(memoryDc, previousObject);
                if (replaced == nint.Zero || replaced == NativeMethods.HgdiError)
                {
                    throw NativeFailure("SelectObject restore");
                }

                if (replaced != bitmap)
                {
                    throw new InvalidOperationException("The DIB was not selected when restoring the memory DC.");
                }
            });
        }

        if (bitmap != nint.Zero)
        {
            TryCleanup(cleanupFailures, () =>
            {
                if (!NativeMethods.DeleteObject(bitmap))
                {
                    throw NativeFailure("DeleteObject");
                }
            });
        }

        if (memoryDc != nint.Zero)
        {
            TryCleanup(cleanupFailures, () =>
            {
                if (!NativeMethods.DeleteDC(memoryDc))
                {
                    throw NativeFailure("DeleteDC");
                }
            });
        }

        if (sourceDc != nint.Zero)
        {
            TryCleanup(cleanupFailures, () =>
            {
                if (NativeMethods.ReleaseDC(sourceOwner, sourceDc) == 0)
                {
                    throw NativeFailure("ReleaseDC");
                }
            });
        }

        if (operationFailure is not null)
        {
            if (cleanupFailures.Count > 0)
            {
                throw new AggregateException("Capture and native resource cleanup failed.", new[] { operationFailure }.Concat(cleanupFailures));
            }

            ExceptionDispatchInfo.Capture(operationFailure).Throw();
        }

        if (cleanupFailures.Count > 0)
        {
            throw new AggregateException("Native resource cleanup failed.", cleanupFailures);
        }

        return result ?? throw new InvalidOperationException("Capture completed without a frame.");
    }

    private static WindowIdentity ReadIdentity(nint hwnd)
    {
        if (!NativeMethods.IsWindow(hwnd))
        {
            throw new InvalidOperationException("The supplied HWND is no longer a window.");
        }

        var threadId = NativeMethods.GetWindowThreadProcessId(hwnd, out var processId);
        if (threadId == 0 || processId == 0)
        {
            throw NativeFailure("GetWindowThreadProcessId");
        }

        using var process = NativeMethods.OpenProcess(
            NativeMethods.ProcessQueryLimitedInformation,
            inheritHandle: false,
            processId);
        if (process.IsInvalid)
        {
            throw NativeFailure("OpenProcess");
        }

        if (!NativeMethods.GetProcessTimes(process, out var creationTime, out _, out _, out _))
        {
            throw NativeFailure("GetProcessTimes");
        }

        var createdUtcTicks = DateTime.FromFileTimeUtc(creationTime.ToInt64()).Ticks;
        var imageName = new StringBuilder(MaxProcessPathLength);
        var imageNameLength = checked((uint)imageName.Capacity);
        if (!NativeMethods.QueryFullProcessImageName(process, 0, imageName, ref imageNameLength))
        {
            throw NativeFailure("QueryFullProcessImageName");
        }

        if (imageNameLength == 0 || imageNameLength > imageName.Capacity)
        {
            throw new InvalidOperationException("The process executable path is unavailable.");
        }

        var currentThreadId = NativeMethods.GetWindowThreadProcessId(hwnd, out var currentProcessId);
        if (!NativeMethods.IsWindow(hwnd) || currentThreadId == 0 || currentProcessId != processId)
        {
            throw new InvalidOperationException("The HWND or owning process changed during identity inspection.");
        }

        return new WindowIdentity(hwnd, processId, createdUtcTicks, imageName.ToString());
    }

    private static ClientGeometry ReadGeometry(nint hwnd)
    {
        if (!NativeMethods.IsWindow(hwnd))
        {
            throw new InvalidOperationException("The supplied HWND is no longer a window.");
        }

        if (!NativeMethods.GetClientRect(hwnd, out var rect))
        {
            throw NativeFailure("GetClientRect");
        }

        var width = checked(rect.Right - rect.Left);
        var height = checked(rect.Bottom - rect.Top);
        if (width <= 0 || height <= 0)
        {
            throw new InvalidOperationException("The client size is unavailable or empty.");
        }

        var origin = new NativeMethods.Point { X = rect.Left, Y = rect.Top };
        if (!NativeMethods.ClientToScreen(hwnd, ref origin))
        {
            throw NativeFailure("ClientToScreen");
        }

        var dpi = NativeMethods.GetDpiForWindow(hwnd);
        if (dpi == 0)
        {
            throw new InvalidOperationException("GetDpiForWindow returned 0; client DPI is unknown.");
        }

        _ = checked(origin.X + width);
        _ = checked(origin.Y + height);
        return new ClientGeometry(width, height, origin.X, origin.Y, dpi);
    }

    private static string ReadTitle(nint hwnd)
    {
        var length = NativeMethods.GetWindowTextLength(hwnd);
        if (length < 0)
        {
            throw NativeFailure("GetWindowTextLength");
        }

        if (length == 0)
        {
            return string.Empty;
        }

        if (length >= MaxWindowTitleLength - 1)
        {
            throw new InvalidOperationException("Window title exceeds the supported native title length.");
        }

        var text = new StringBuilder(length + 1);
        var copied = NativeMethods.GetWindowText(hwnd, text, text.Capacity);
        if (copied == 0)
        {
            throw NativeFailure("GetWindowText");
        }

        return text.ToString();
    }

    private static ClientRect ClipToClient(ClientRect requested, ClientGeometry geometry)
    {
        if (requested.Right <= requested.Left || requested.Bottom <= requested.Top)
        {
            throw new ArgumentOutOfRangeException(nameof(requested), "Client bounds must have positive half-open dimensions.");
        }

        var clipped = new ClientRect(
            Math.Max(0, requested.Left),
            Math.Max(0, requested.Top),
            Math.Min(geometry.Width, requested.Right),
            Math.Min(geometry.Height, requested.Bottom));
        if (clipped.Right <= clipped.Left || clipped.Bottom <= clipped.Top)
        {
            throw new ArgumentOutOfRangeException(nameof(requested), "Client bounds do not intersect the client area.");
        }

        return clipped;
    }

    private static void EnsureSameContext(WindowSnapshot expected, WindowSnapshot actual)
    {
        if (!SameIdentity(expected.Identity, actual.Identity) || expected.Geometry != actual.Geometry)
        {
            throw new InvalidOperationException("Window identity or client geometry changed; discard the capture.");
        }
    }

    private static bool SameIdentity(WindowIdentity left, WindowIdentity right) =>
        left.Hwnd == right.Hwnd
        && left.ProcessId == right.ProcessId
        && left.ProcessCreationUtcTicks == right.ProcessCreationUtcTicks
        && string.Equals(left.ExecutablePath, right.ExecutablePath, StringComparison.OrdinalIgnoreCase);

    private static bool IsIdentityVTitle(string title)
    {
        var normalized = title.Trim();
        if (string.Equals(normalized, "第五人格", StringComparison.Ordinal))
        {
            return true;
        }

        return HasTitlePrefix(normalized, "identity v") || HasTitlePrefix(normalized, "identityv");
    }

    private static bool HasTitlePrefix(string title, string prefix)
    {
        if (string.Equals(title, prefix, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        return title.Length > prefix.Length
            && title.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
            && IdentityVSeparators.Contains(title[prefix.Length], StringComparison.Ordinal);
    }

    private static void ValidateHwnd(nint hwnd)
    {
        if (hwnd == nint.Zero)
        {
            throw new ArgumentException("HWND must be non-zero.", nameof(hwnd));
        }
    }

    private static Exception NativeFailure(string operation)
    {
        var error = Marshal.GetLastWin32Error();
        return error != 0
            ? new Win32Exception(error, $"{operation} failed.")
            : new InvalidOperationException($"{operation} failed without a Win32 error code.");
    }

    private static void TryCleanup(ICollection<Exception> failures, Action cleanup)
    {
        try
        {
            cleanup();
        }
        catch (Exception error)
        {
            failures.Add(error);
        }
    }
}
