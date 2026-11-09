using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using StarMap.App.Platform;

internal static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        int option = Array.IndexOf(args, "--output");
        string output = option >= 0 && option + 1 < args.Length ? Path.GetFullPath(args[option + 1]) : throw new ArgumentException("--output is required");
        if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Preserve prior capture evidence; use a new output.");
        Directory.CreateDirectory(output);
        var app = new Application();
        var scene = new Grid();
        scene.RowDefinitions.Add(new()); scene.RowDefinitions.Add(new()); scene.ColumnDefinitions.Add(new()); scene.ColumnDefinitions.Add(new());
        foreach (var (color, row, column) in new[] { (Colors.Red, 0, 0), (Colors.Lime, 0, 1), (Colors.Blue, 1, 0), (Colors.Yellow, 1, 1) })
        {
            var block = new Border { Background = new SolidColorBrush(color) }; Grid.SetRow(block, row); Grid.SetColumn(block, column); scene.Children.Add(block);
        }
        var window = new Window { Title = "星图 · 自有采集验证", Width = 520, Height = 380, Content = scene, ResizeMode = ResizeMode.NoResize };
        window.Loaded += async (_, _) =>
        {
            var checks = new List<object>(); int exit = 0;
            void Check(bool passed, string name)
            {
                checks.Add(new { name, passed }); Console.WriteLine((passed ? "ok " : "FAIL ") + name);
                if (!passed) throw new InvalidDataException(name);
            }
            async Task Paint() { await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); await Task.Delay(250); }
            using var capture = new WindowsClientCapture();
            try
            {
                await Paint();
                var binding = capture.BindOwnedFixture(new WindowInteropHelper(window).Handle);
                var frame = await capture.CaptureAsync(binding);
                capture.ValidateForCommit(frame);
                Check(frame.Image.IsFrozen && frame.Metadata.After.Width > 0 && frame.Metadata.After.Height > 0, "owned-client-dib-frozen-image");
                int width = frame.Metadata.After.Width, height = frame.Metadata.After.Height;
                byte[] bytes = frame.Bgra32Pixels.ToArray();
                bool Pixel(int x, int y, byte r, byte g, byte b)
                {
                    int at = checked((y * width + x) * 4); return bytes[at] == b && bytes[at + 1] == g && bytes[at + 2] == r && bytes[at + 3] == 255;
                }
                Check(Pixel(width / 4, height / 4, 255, 0, 0) && Pixel(width * 3 / 4, height / 4, 0, 255, 0) && Pixel(width / 4, height * 3 / 4, 0, 0, 255) && Pixel(width * 3 / 4, height * 3 / 4, 255, 255, 0), "four-independent-quadrant-pixels");
                using var canceled = new CancellationTokenSource(); canceled.Cancel(); bool canceledObserved = false;
                try { await capture.CaptureAsync(binding, canceled.Token); } catch (OperationCanceledException) { canceledObserved = true; }
                Check(canceledObserved, "precanceled-request-rejected");
                window.Width += 44; await Paint(); bool geometryRejected = false;
                try { capture.ValidateForCommit(frame); } catch (ClientCaptureException) { geometryRejected = true; }
                Check(geometryRejected, "geometry-change-invalidates-old-frame");
                capture.Unbind(); bool unboundRejected = false;
                try { capture.ValidateBinding(binding); } catch (ClientCaptureException) { unboundRejected = true; }
                Check(unboundRejected, "unbound-window-cannot-revalidate");
                binding = capture.BindOwnedFixture(new WindowInteropHelper(window).Handle);
                bool invalidBindRejected = false;
                try { capture.BindOwnedFixture(0); } catch (ClientCaptureException) { invalidBindRejected = true; }
                bool oldAfterFailureRejected = false;
                try { capture.ValidateBinding(binding); } catch (ClientCaptureException) { oldAfterFailureRejected = true; }
                Check(invalidBindRejected && oldAfterFailureRejected, "failed-new-bind-revokes-old-binding");
                binding = capture.BindOwnedFixture(new WindowInteropHelper(window).Handle);
                var staleCandidate = new WindowCandidate(binding.Identity with { ProcessStartUtcTicks = binding.Identity.ProcessStartUtcTicks - 1 }, binding.GeometryAtBinding);
                bool staleCandidateRejected = false;
                try { capture.BindOwnedFixture(staleCandidate); } catch (ClientCaptureException) { staleCandidateRejected = true; }
                bool oldAfterStaleRejected = false;
                try { capture.ValidateBinding(binding); } catch (ClientCaptureException) { oldAfterStaleRejected = true; }
                Check(staleCandidateRejected && oldAfterStaleRejected, "stale-candidate-identity-cannot-authorize-current-hwnd");
                scene.Children.Clear(); scene.Background = Brushes.Black; await Paint();
                binding = capture.BindOwnedFixture(new WindowInteropHelper(window).Handle);
                bool blackRejected = false;
                try { await capture.CaptureAsync(binding); } catch (ClientCaptureException error) { blackRejected = error.Code.Contains("blank", StringComparison.OrdinalIgnoreCase); }
                Check(blackRejected, "black-client-rejected");
            }
            catch (Exception error) { exit = 1; checks.Add(new { name = "failure", passed = false, error = error.ToString() }); Console.Error.WriteLine(error); }
            finally
            {
                await File.WriteAllTextAsync(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new { exit, checks, scope = "Linked production capture source; owned WPF fixture only, no game/input/WGC/overlay acceptance" }, new JsonSerializerOptions { WriteIndented = true }));
                app.Shutdown(exit);
            }
        };
        app.Run(window);
    }
}
