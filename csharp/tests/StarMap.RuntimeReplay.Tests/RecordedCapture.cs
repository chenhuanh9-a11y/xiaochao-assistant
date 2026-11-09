using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Runtime;

// Shared by Runtime and WPF replay. Only the OS capture port is replaced.
internal sealed class RecordedCapture(Dictionary<string, JsonObject> index) : ICapturePort
{
    JsonObject? selected; CaptureStamp? current; long generation = 1, sequence;
    Hold? nextHold;
    public sealed record Hold(TaskCompletionSource Started, TaskCompletionSource Release);
    public Hold HoldNext() => nextHold = new(new(TaskCreationOptions.RunContinuationsAsynchronously), new(TaskCreationOptions.RunContinuationsAsynchronously));
    public int Calls { get; private set; }
    public bool IsBound => true;
    public CaptureBindingScope? BindingScope => new("recorded-only", generation);
    public void Select(string id, bool invalidateBinding = true) { selected = index[id]; if (invalidateBinding) Advance(); }
    public void Advance() { generation++; current = null; }
    public bool IsCurrent(CaptureStamp stamp) => current == stamp && stamp.Generation == generation;
    public async ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested(); Calls++;
        if (nextHold is { } hold)
        {
            nextHold = null; hold.Started.TrySetResult();
            await hold.Release.Task.WaitAsync(cancellation);
        }
        var sample = selected ?? throw new InvalidOperationException("No recorded input selected.");
        var captured = DateTimeOffset.Parse(sample["capturedAt"]!.GetValue<string>());
        if (captured < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero)) throw new InvalidDataException("Old UI capture rejected.");
        using var bgr = Cv2.ImDecode(File.ReadAllBytes(sample["image"]!.GetValue<string>()), ImreadModes.Color);
        using var bgra = new Mat(); Cv2.CvtColor(bgr, bgra, ColorConversionCodes.BGR2BGRA);
        int width = bgra.Cols, height = bgra.Rows, stride = checked(width * 4);
        var pixels = new byte[checked(stride * height)];
        for (int row = 0; row < height; row++) Marshal.Copy(bgra.Ptr(row), pixels, row * stride, stride);
        var geometry = sample["physicalClient"]!;
        if (geometry["Width"]!.GetValue<int>() != width || geometry["Height"]!.GetValue<int>() != height)
            throw new InvalidDataException("Recorded physical client disagrees with actual PNG.");
        current = new("recorded-only", generation, "recorded-frame-" + ++sequence);
        return new ClientCaptureResult(new(pixels, width, height, stride, current, captured,
            new(CaptureEvidenceKind.RecordedClient, new(geometry["Left"]!.GetValue<int>(), geometry["Top"]!.GetValue<int>(),
                width, height, geometry["Dpi"]!.GetValue<uint>()), "preserved-client-png")));
    }
}
