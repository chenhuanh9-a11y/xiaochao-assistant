using System.ComponentModel;
using System.Runtime.InteropServices;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX.Direct3D11;

namespace StarMap.App.Platform;

/// <summary>Small system-API interop boundary; no external D3D library or Windows App SDK.</summary>
internal static class WindowsGraphicsCaptureInterop
{
    [StructLayout(LayoutKind.Sequential)] struct Rect { internal int Left, Top, Right, Bottom; }
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] static extern bool GetWindowRect(nint window, out Rect rect);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(nint window, uint attribute, out Rect rect, uint size);
    [DllImport("combase.dll")] static extern int RoInitialize(uint mode);
    [DllImport("combase.dll")] static extern void RoUninitialize();
    [DllImport("combase.dll", CharSet = CharSet.Unicode, ExactSpelling = true)] static extern int WindowsCreateString(string text, uint length, out nint value);
    [DllImport("combase.dll")] static extern int WindowsDeleteString(nint value);
    [DllImport("combase.dll")] static extern int RoGetActivationFactory(nint name, in Guid iid, out nint factory);
    [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(nint adapter, uint driverType, nint software,
        uint flags, nint featureLevels, uint levelCount, uint sdkVersion, out nint device, out uint featureLevel, out nint context);
    [DllImport("d3d11.dll")] static extern int CreateDirect3D11DeviceFromDXGIDevice(nint dxgi, out nint inspectable);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CreateForWindow(nint factory, nint window, in Guid iid, out nint item);

    internal sealed class Apartment : IDisposable
    {
        bool initialized;
        internal Apartment()
        {
            Marshal.ThrowExceptionForHR(RoInitialize(1)); // The owning worker is MTA; uninitialize on this same thread.
            initialized = true;
        }
        public void Dispose() { if (initialized) { initialized = false; RoUninitialize(); } }
    }

    internal static WindowsGraphicsCaptureBounds Bounds(WindowIdentity identity, bool requireForeground)
    {
        using var dpi = CaptureNative.PhysicalCoordinates();
        CaptureNative.ValidateIdentity(identity, requireForeground);
        var client = CaptureNative.Geometry(identity.Handle);
        if (!GetWindowRect(identity.Handle, out var window)) throw new Win32Exception(Marshal.GetLastWin32Error(), "WGC GetWindowRect 失败。");
        var dwm = DwmGetWindowAttribute(identity.Handle, 9, out var extended, (uint)Marshal.SizeOf<Rect>());
        CaptureNative.ValidateIdentity(identity, requireForeground);
        return new(client, new(window.Left, window.Top, window.Right, window.Bottom),
            dwm >= 0 ? new(extended.Left, extended.Top, extended.Right, extended.Bottom) : null, dwm);
    }

    internal static GraphicsCaptureItem ItemForWindow(nint window)
    {
        nint name = 0, factory = 0, item = 0;
        try
        {
            const string type = "Windows.Graphics.Capture.GraphicsCaptureItem";
            Marshal.ThrowExceptionForHR(WindowsCreateString(type, (uint)type.Length, out name));
            var interop = new Guid("3628e81b-3cac-4c60-b7f4-23ce0e0c3356"); // IGraphicsCaptureItemInterop : IUnknown.
            Marshal.ThrowExceptionForHR(RoGetActivationFactory(name, in interop, out factory));
            var method = Marshal.GetDelegateForFunctionPointer<CreateForWindow>(Marshal.ReadIntPtr(Marshal.ReadIntPtr(factory), 3 * IntPtr.Size));
            var iid = new Guid("79c3f95b-31f7-4ec2-a464-632ef5d30760"); // SDK IGraphicsCaptureItem.
            Marshal.ThrowExceptionForHR(method(factory, window, in iid, out item));
            return WinRT.MarshalInspectable<GraphicsCaptureItem>.FromAbi(item);
        }
        finally
        {
            if (item != 0) Marshal.Release(item);
            if (factory != 0) Marshal.Release(factory);
            if (name != 0) WindowsDeleteString(name);
        }
    }

    internal sealed class Device : IDisposable
    {
        nint native;
        internal IDirect3DDevice Projected { get; }
        internal Device()
        {
            nint context = 0, dxgi = 0, inspectable = 0;
            try
            {
                Marshal.ThrowExceptionForHR(D3D11CreateDevice(0, 1, 0, 0x20, 0, 0, 7, out native, out _, out context)); // hardware + BGRA_SUPPORT.
                var iid = new Guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c"); // IDXGIDevice.
                Marshal.ThrowExceptionForHR(Marshal.QueryInterface(native, in iid, out dxgi));
                Marshal.ThrowExceptionForHR(CreateDirect3D11DeviceFromDXGIDevice(dxgi, out inspectable));
                Projected = WinRT.MarshalInterface<IDirect3DDevice>.FromAbi(inspectable);
            }
            catch { if (native != 0) { Marshal.Release(native); native = 0; } throw; }
            finally
            {
                if (inspectable != 0) Marshal.Release(inspectable);
                if (dxgi != 0) Marshal.Release(dxgi);
                if (context != 0) Marshal.Release(context);
            }
        }
        public void Dispose()
        {
            if (native == 0) return;
            try { Projected.Dispose(); }
            finally { Marshal.Release(native); native = 0; }
        }
    }
}
