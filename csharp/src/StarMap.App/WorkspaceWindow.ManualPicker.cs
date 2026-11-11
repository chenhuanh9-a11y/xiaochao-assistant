using System.ComponentModel;
using System.Windows;
using StarMap.App.Platform;

namespace StarMap.App;

public partial class WorkspaceWindow
{
    ManualMapPickerPlacement? pickerPlacement;
    Window? placedPicker;
    void PlaceManualPicker(Window window)
    {
        PickerPhysicalRect? rectangle = null;
        long? bindingGeneration = runtimeCapture.Binding?.Generation;
        if (runtimeCapture.Binding is { } binding)
        {
            try
            {
                CaptureNative.ValidateIdentity(binding.Identity, requireForeground: false);
                using var physical = CaptureNative.PhysicalCoordinates();
                var client = CaptureNative.Geometry(binding.Identity.Handle);
                rectangle = new(client.Left, client.Top, client.Width, client.Height);
            }
            catch (Exception error) when (error is Win32Exception or ClientCaptureException or InvalidOperationException) { }
        }
        if (!ReferenceEquals(window, placedPicker))
        { pickerPlacement?.Dispose(); placedPicker = window; pickerPlacement = new(window, message => Status.Text = message); }
        pickerPlacement!.Request(rectangle, () => !closed && !closing && runtimeCapture.Binding?.Generation == bindingGeneration);
    }
}
