using System.Globalization;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows.Media.Imaging;
using StarMap.App.Platform;

internal static class LiveCapture
{
    internal static int Run(string[] args, string output)
    {
        int option = Array.IndexOf(args, "--capture-window");
        long handle = long.Parse(args[option + 1], CultureInfo.InvariantCulture);
        DateTimeOffset started = DateTimeOffset.UtcNow;
        long utcAnchorQpc = Stopwatch.GetTimestamp();
        try
        {
            var candidates = WindowsClientCapture.EnumerateCandidates(128);
            var target = candidates.Items.Single(item => item.Handle == (nint)handle
                && item.ProcessName == "dwrg" && item.Title == "第五人格");
            using var capture = new WindowsClientCapture();
            var binding = capture.Bind(target);
            var frame = capture.CaptureAsync(binding).GetAwaiter().GetResult();
            capture.ValidateForCommit(frame);
            var capturedAt = started + TimeSpan.FromSeconds(
                (frame.Metadata.PixelsCopiedQpc - utcAnchorQpc) / (double)frame.Metadata.QpcFrequency);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(frame.Image));
            using (var file = new FileStream(Path.Combine(output, "client.png"), FileMode.CreateNew)) encoder.Save(file);
            File.WriteAllText(Path.Combine(output, "capture.json"), JsonSerializer.Serialize(new {
                eventKind = "live-client-capture", evidenceKind = "live-client-capture", capturedAt,
                startedAt = started, completedAt = DateTimeOffset.UtcNow, samplePath = "client.png",
                imageScope = "Full physical target client area only; no title bar, taskbar, resizing or pixel edits",
                Identity = new { handle = binding.Identity.Handle.ToInt64(), binding.Identity.ProcessId,
                    binding.Identity.ThreadId, binding.Identity.ProcessStartUtcTicks, binding.Identity.WindowClass,
                    binding.Identity.ProcessName, binding.Identity.Title }, binding.Generation, frame.Metadata,
                scope = "Explicit single GDI capture; map-open/floor/identity and WGC are not proven by capture alone"
            }, new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine(JsonSerializer.Serialize(new { success = true, width = frame.Metadata.After.Width,
                height = frame.Metadata.After.Height, dpi = frame.Metadata.After.Dpi, capturedAt, frame.Metadata.Statistics }));
            return 0;
        }
        catch (Exception error)
        {
            File.WriteAllText(Path.Combine(output, "capture-error.json"), JsonSerializer.Serialize(new {
                startedAt = started, recordedAt = DateTimeOffset.UtcNow, type = error.GetType().Name,
                code = (error as ClientCaptureException)?.Code, error.Message
            }, new JsonSerializerOptions { WriteIndented = true }));
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }
}
