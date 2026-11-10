using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text.Json;
using StarMap.Bridge;
using StarMap.Desktop;
using StarMap.Windows;

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        var nativeOnly = args.Length == 1 && args[0] == "--native-only";
        var simulatedForeground = args.Length == 4 && args[3] == "--simulate-foreground";
        if (!nativeOnly && args.Length != 3 && !simulatedForeground)
        {
            Console.Error.WriteLine("usage: StarMap.NativeTests --native-only | SOURCE_ROOT PYTHON RESOURCE_ROOT [--simulate-foreground]");
            return 2;
        }
        using var testWindow = new SelfTestWindow();
        HybridWorker? worker = null;
        NativeCaptureService? service = null;
        try
        {
            var initial = WindowCapture.Inspect(testWindow.Hwnd);
            Check(initial.Identity.ProcessId == (uint)Environment.ProcessId, "Inspect returned a different process.");
            Check(initial.Visible && !initial.Minimized, "The owned test window is not visible and unminimized.");
            Check(initial.Geometry.Width > 600 && initial.Geometry.Height > 300 && initial.Geometry.Dpi > 0,
                "The owned test window has unknown or too-small client geometry.");
            Check(!WindowCapture.IsIdentityV(initial), "The test process unexpectedly passed production Identity V policy.");
            var foregroundAvailable = testWindow.TryActivateOwnWindow();
            Console.WriteLine(foregroundAvailable
                ? "Owned test window is foreground."
                : "Windows denied foreground activation of the owned test window; direct GetDC(hwnd) checks can proceed, production capture will retain the foreground refusal.");

            await CheckDefaultPolicyAsync(testWindow.Hwnd);
            var current = RunDirectGdiChecks(testWindow, initial);
            Console.WriteLine($"PASS owned GDI frame: hwnd=0x{current.Identity.Hwnd.ToInt64():X}, pid={current.Identity.ProcessId}, client={current.Geometry.Width}x{current.Geometry.Height}, origin=({current.Geometry.Left},{current.Geometry.Top}), dpi={current.Geometry.Dpi}");

            if (nativeOnly)
            {
                Console.WriteLine("Native-only run complete; no Python worker was started.");
                return 0;
            }

            var repositoryRoot = Path.GetFullPath(args[0]);
            var python = Path.GetFullPath(args[1]);
            var workerScript = Path.Combine(repositoryRoot, "hybrid", "python", "runtime_worker.py");
            var resourceRoot = Path.GetFullPath(args[2]);
            var testRoot = Path.Combine(repositoryRoot, "work", "hybrid-native-tests", Guid.NewGuid().ToString("N"));
            var dataRoot = Path.Combine(testRoot, "data");
            var frameRoot = Path.Combine(testRoot, "frames");
            RequireFile(python);
            RequireFile(workerScript);
            RequireDirectory(resourceRoot);
            Directory.CreateDirectory(dataRoot);
            Directory.CreateDirectory(frameRoot);

            var traces = new ConcurrentQueue<CaptureTrace>();
            if (simulatedForeground)
                Console.WriteLine("SIMULATION: only the foreground HWND fact is substituted; actual owned-window GDI pixels and production Python remain. This is not native foreground/game acceptance.");
            service = new NativeCaptureService(
                fullCapture: (snapshot, cancellation) => Task.Run(() =>
                    RecordCapture(traces, "full-client", WindowCapture.CaptureClient(snapshot, cancellation),
                        new ClientRect(0, 0, snapshot.Geometry.Width, snapshot.Geometry.Height)), cancellation),
                capture: (snapshot, roi, cancellation) => Task.Run(() =>
                    RecordCapture(traces, "client-region", WindowCapture.CaptureRegion(snapshot, roi, cancellation), roi), cancellation),
                inspect: simulatedForeground ? hwnd => WindowCapture.Inspect(hwnd) with { ForegroundHwnd = hwnd } : null,
                targetPolicy: snapshot => snapshot.Identity.ProcessId == (uint)Environment.ProcessId);

            worker = HybridWorker.Start(python, workerScript, resourceRoot, dataRoot, frameRoot,
                requestTimeout: TimeSpan.FromSeconds(60), captureService: service);
            var hello = await worker.HelloAsync();
            Check(worker.EvidenceKind == "live-client-capture", "HybridWorker did not launch the native-pipe worker path.");
            Check(worker.SupportsRuntime && hello.Operations?.Contains("set_frame", StringComparer.Ordinal) == false,
                "Native worker must advertise runtime without recorded set_frame support.");
            Console.WriteLine($"Worker ready: pid={worker.ProcessId}, evidence={worker.EvidenceKind}, python={hello.Python}, operations=[{string.Join(',', hello.Operations ?? [])}]");

            var prepared = await worker.InvokeAsync("prepare", new { season = "s0", mode = "difficult" });
            Console.WriteLine($"prepare s0/difficult: {prepared.GetRawText()}");
            Check(prepared.TryGetProperty("capability", out var capability)
                && capability.TryGetProperty("available", out var available)
                && available.ValueKind == JsonValueKind.True,
                "The supplied real resource copy did not prepare s0/difficult.");
            Check(service.PixelCalls == 0 && traces.IsEmpty,
                "prepare or metadata preflight acquired pixels.");

            foregroundAvailable = testWindow.TryActivateOwnWindow();
            var selected = service.SelectTarget(testWindow.Hwnd);
            Check(selected.Identity.ProcessId == (uint)Environment.ProcessId,
                "The test-only target policy selected a process other than this test host.");
            Check(service.CurrentTarget()?.Identity.Hwnd == testWindow.Hwnd,
                "NativeCaptureService did not retain the owned test HWND.");
            Console.WriteLine("Test-only targetPolicy: Identity.ProcessId == this test host PID; production default remains strict.");

            var bind = await worker.InvokeAsync("dispatch", new
            {
                action = "bind_capture",
                payload = new { hwnd = testWindow.Hwnd.ToInt64() },
            });
            ReportReceipt("bind_capture", bind);
            Check(ReceiptSucceeded(bind), "Production bind_capture failed.");
            Check(service.PixelCalls == 0 && traces.IsEmpty,
                "bind_capture metadata preflight unexpectedly acquired pixels.");

            var start = await worker.InvokeAsync("dispatch", new { action = "start_round" });
            ReportReceipt("start_round", start);
            Check(ReceiptSucceeded(start), "Production start_round failed.");
            Check(service.PixelCalls == 0 && traces.IsEmpty,
                "start_round unexpectedly acquired pixels.");

            foregroundAvailable = testWindow.TryActivateOwnWindow();
            string? dispatchFailure = null;
            try
            {
                var capture = await worker.InvokeAsync("dispatch", new { action = "capture_now" });
                ReportReceipt("capture_now", capture);
                dispatchFailure = ReadReceiptReason(capture);
            }
            catch (Exception error)
            {
                dispatchFailure = $"{error.GetType().Name}:{error.Message}";
                Console.WriteLine($"capture_now returned an exception after entering the production worker: {dispatchFailure}");
            }

            var acquired = traces.ToArray();
            if (foregroundAvailable || simulatedForeground)
            {
                Check(service.PixelCalls > 0,
                    "capture_now did not enter a native pixel acquisition. " + (dispatchFailure ?? "no worker error was returned."));
                Check(acquired.Length > 0,
                    "The native service counted a pixel call, but no actual GDI frame completed. " + (dispatchFailure ?? ""));
                foreach (var frame in acquired)
                {
                    Check(frame.Frame.Width == selected.Geometry.Width && frame.Frame.Height == selected.Geometry.Height,
                        $"Production native frame was not complete-client size: {frame.Frame.Width}x{frame.Frame.Height}.");
                    Check(frame.Frame.ClientBounds == new ClientRect(0, 0, selected.Geometry.Width, selected.Geometry.Height),
                        $"Production native frame used a crop: {frame.Frame.ClientBounds}.");
                    Check(frame.Frame.Context.Geometry == selected.Geometry,
                        "Production native frame context differs from selected physical client geometry.");
                    CheckKnownColorChart(frame.Frame);
                }
            }
            else
            {
                Check(service.PixelCalls == 0 && acquired.Length == 0,
                    "Native pixels were acquired despite Windows withholding foreground for the owned test window.");
                Console.WriteLine($"capture_now retained foreground refusal and acquired no pixels: {dispatchFailure ?? "no worker failure was returned"}");
            }
            Check(service.Failure is null, "Native callback service failed: " + service.Failure);
            Console.WriteLine($"Native pipe result: realForeground={foregroundAvailable}, simulatedForeground={simulatedForeground}, calls={service.PixelCalls}, completedFrames={acquired.Length}, result={dispatchFailure ?? "rank/dispatch returned normally"}");

            var end = await worker.InvokeAsync("dispatch", new { action = "end_round" });
            ReportReceipt("end_round", end);

            var workerPid = worker.ProcessId;
            var observedTree = ProcessTree.SnapshotDescendants((uint)workerPid);
            Console.WriteLine("HybridWorker/native service DisposeAsync starting.");
            await worker.DisposeAsync();
            Console.WriteLine("HybridWorker/native service DisposeAsync returned.");
            worker = null;
            Check(service.Failure is null, "Native callback service did not close cleanly: " + service.Failure);
            Check(service.PixelCalls == acquired.Length,
                $"Native service call count {service.PixelCalls} differs from completed GDI frames {acquired.Length}.");
            CheckNoObservedWorkerTree(observedTree, workerPid);
            Console.WriteLine($"PASS EOF and process-tree shutdown: workerPid={workerPid}, observedPids=[{string.Join(',', observedTree.Order())}]");

            Console.WriteLine("No map recognition or alignment acceptance is claimed for the synthetic color chart.");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"FAIL {error.GetType().FullName}: {error.Message}");
            Console.Error.WriteLine(error);
            if (service?.Failure is { } serviceFailure)
            {
                Console.Error.WriteLine("Native service failure:");
                Console.Error.WriteLine(serviceFailure);
            }
            return 1;
        }
        finally
        {
            if (worker is not null)
            {
                try
                {
                    Console.WriteLine("Failure-path HybridWorker/native service DisposeAsync starting.");
                    await worker.DisposeAsync();
                    Console.WriteLine($"Failure-path HybridWorker/native service DisposeAsync returned; exited={worker.HasExited}.");
                }
                catch (Exception error) { Console.Error.WriteLine($"Worker cleanup failure: {error}"); }
            }
            else if (service is not null)
            {
                try
                {
                    Console.WriteLine("Standalone NativeCaptureService DisposeAsync starting.");
                    await service.DisposeAsync();
                    Console.WriteLine("Standalone NativeCaptureService DisposeAsync returned.");
                }
                catch (Exception error) { Console.Error.WriteLine($"Native service cleanup failure: {error}"); }
            }
        }
    }

    private static async Task CheckDefaultPolicyAsync(nint hwnd)
    {
        var strictService = new NativeCaptureService((snapshot, cancellation) =>
            Task.Run(() => WindowCapture.CaptureClient(snapshot, cancellation), cancellation));
        try
        {
            try
            {
                strictService.SelectTarget(hwnd);
                throw new InvalidOperationException("Default NativeCaptureService policy accepted the non-game test process.");
            }
            catch (InvalidOperationException error) when (error.Message.Contains("不符合游戏标题", StringComparison.Ordinal))
            {
                Console.WriteLine("PASS default NativeCaptureService target policy rejected the owned non-game window.");
            }
        }
        finally
        {
            Console.WriteLine("Strict NativeCaptureService DisposeAsync starting.");
            await strictService.DisposeAsync();
            Console.WriteLine("Strict NativeCaptureService DisposeAsync returned.");
        }
    }

    private static WindowSnapshot RunDirectGdiChecks(SelfTestWindow testWindow, WindowSnapshot initial)
    {
        Console.WriteLine("Direct GDI: Inspect refresh before CaptureClient starting.");
        var refreshed = WindowCapture.Inspect(testWindow.Hwnd);
        Check(refreshed.Identity == initial.Identity && refreshed.Geometry == initial.Geometry,
            "The direct test window changed before the first pixel acquisition.");
        Console.WriteLine("Direct GDI: Inspect refresh returned.");
        Console.WriteLine("Direct GDI: starting CaptureClient.");
        var frame = WindowCapture.CaptureClient(initial);
        Console.WriteLine("Direct GDI: CaptureClient returned.");
        Check(frame.Width == initial.Geometry.Width && frame.Height == initial.Geometry.Height,
            "CaptureClient did not return the full client dimensions.");
        Check(frame.Stride == checked(frame.Width * 4) && frame.Bgra32.Length == checked(frame.Stride * frame.Height),
            "CaptureClient returned invalid owned BGRA dimensions.");
        Check(frame.Context.Identity == initial.Identity && frame.Context.Geometry == initial.Geometry,
            "CaptureClient context did not bind the inspected identity and geometry.");
        Check(frame.Acquisition == "client-dc-bitblt", "CaptureClient did not report its client-DC acquisition.");
        CheckKnownColorChart(frame);

        var roi = new ClientRect(frame.Width / 8, frame.Height / 4, frame.Width / 4, frame.Height / 2);
        Console.WriteLine("Direct GDI: starting CaptureRegion.");
        var region = WindowCapture.CaptureRegion(initial, roi);
        Console.WriteLine("Direct GDI: CaptureRegion returned.");
        Check(region.ClientBounds == roi && region.Width == roi.Width && region.Height == roi.Height,
            "CaptureRegion did not preserve the requested half-open client ROI.");
        CheckBgra(region, region.Width / 2, region.Height / 2, expectedBlue: 0, expectedGreen: 0,
            expectedRed: 255, expectedAlpha: 255, "ROI red block");

        using (var cancelled = new CancellationTokenSource())
        {
            cancelled.Cancel();
            try
            {
                _ = WindowCapture.CaptureClient(initial, cancelled.Token);
                throw new InvalidOperationException("A pre-cancelled native capture returned a frame.");
            }
            catch (OperationCanceledException)
            {
                Console.WriteLine("PASS pre-cancelled capture returned no frame.");
            }
        }

        var warmCount = NativeWindowApi.GetGuiResources(NativeWindowApi.GetCurrentProcess(), 0);
        Console.WriteLine($"Direct GDI: repeated-capture baseline={warmCount}.");
        for (var index = 0; index < 12; index++)
        {
            var repeated = WindowCapture.CaptureClient(initial);
            CheckKnownColorChart(repeated);
        }
        var finalCount = NativeWindowApi.GetGuiResources(NativeWindowApi.GetCurrentProcess(), 0);
        Check(warmCount == finalCount,
            $"GDI object count changed across 12 captures: before={warmCount}, after={finalCount}.");
        Console.WriteLine($"PASS GDI handle stability: gdiObjects={warmCount} before/after 12 captures.");

        testWindow.MoveOwnWindow();
        var moved = WindowCapture.Inspect(testWindow.Hwnd);
        Check(moved.Geometry.Left != initial.Geometry.Left || moved.Geometry.Top != initial.Geometry.Top,
            "MoveWindow did not change the owned client's physical origin.");
        try
        {
            _ = WindowCapture.CaptureClient(initial);
            throw new InvalidOperationException("CaptureClient accepted a stale pre-move snapshot.");
        }
        catch (InvalidOperationException error) when (error.Message.Contains("geometry changed", StringComparison.Ordinal))
        {
            Console.WriteLine("PASS stale geometry snapshot was rejected after moving the owned window.");
        }

        testWindow.TryActivateOwnWindow();
        var movedFrame = WindowCapture.CaptureClient(moved);
        CheckKnownColorChart(movedFrame);
        return moved;
    }

    private static CapturedClientFrame RecordCapture(
        ConcurrentQueue<CaptureTrace> traces,
        string kind,
        CapturedClientFrame frame,
        ClientRect requested)
    {
        traces.Enqueue(new CaptureTrace(kind, requested, frame));
        return frame;
    }

    private static void CheckKnownColorChart(CapturedClientFrame frame)
    {
        var y = frame.Height / 2;
        CheckBgra(frame, frame.Width / 6, y, expectedBlue: 0, expectedGreen: 0,
            expectedRed: 255, expectedAlpha: 255, "red block");
        CheckBgra(frame, frame.Width / 2, y, expectedBlue: 0, expectedGreen: 255,
            expectedRed: 0, expectedAlpha: 255, "green block");
        CheckBgra(frame, (frame.Width * 5) / 6, y, expectedBlue: 255, expectedGreen: 0,
            expectedRed: 0, expectedAlpha: 255, "blue block");
    }

    private static void CheckBgra(
        CapturedClientFrame frame,
        int x,
        int y,
        byte expectedBlue,
        byte expectedGreen,
        byte expectedRed,
        byte expectedAlpha,
        string description)
    {
        var pixels = frame.Bgra32.Span;
        var offset = checked(y * frame.Stride + x * 4);
        Check(pixels[offset] == expectedBlue && pixels[offset + 1] == expectedGreen
            && pixels[offset + 2] == expectedRed && pixels[offset + 3] == expectedAlpha,
            $"{description} pixel mismatch: BGRA=({pixels[offset]},{pixels[offset + 1]},{pixels[offset + 2]},{pixels[offset + 3]}).");
    }

    private static bool ReceiptSucceeded(JsonElement response) =>
        response.TryGetProperty("receipt", out var receipt)
        && receipt.TryGetProperty("success", out var success)
        && success.ValueKind == JsonValueKind.True;

    private static string? ReadReceiptReason(JsonElement response) =>
        response.TryGetProperty("receipt", out var receipt)
        && receipt.TryGetProperty("reason", out var reason)
        && reason.ValueKind == JsonValueKind.String ? reason.GetString() : null;

    private static void ReportReceipt(string operation, JsonElement response)
    {
        var reason = ReadReceiptReason(response) ?? "(no receipt reason)";
        var success = ReceiptSucceeded(response);
        Console.WriteLine($"{operation}: success={success}, reason={reason}");
    }

    private static void CheckNoObservedWorkerTree(HashSet<uint> observedPids, int workerPid)
    {
        var deadline = Stopwatch.StartNew();
        HashSet<uint> live;
        do
        {
            live = ProcessTree.SnapshotAllProcessIds();
            if (!observedPids.Any(live.Contains)) return;
            Thread.Sleep(100);
        } while (deadline.Elapsed < TimeSpan.FromSeconds(5));

        var remaining = observedPids.Where(live.Contains).Order().ToArray();
        throw new IOException($"Python worker process tree remained after EOF shutdown. workerPid={workerPid}, remaining=[{string.Join(',', remaining)}]");
    }

    private static void RequireFile(string path)
    {
        if (!File.Exists(path)) throw new FileNotFoundException("Required test input is missing.", path);
    }

    private static void RequireDirectory(string path)
    {
        if (!Directory.Exists(path)) throw new DirectoryNotFoundException("Required test input directory is missing: " + path);
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private sealed record CaptureTrace(string Kind, ClientRect RequestedBounds, CapturedClientFrame Frame);
}

internal sealed class SelfTestWindow : IDisposable
{
    private readonly string className = "StarMap.NativeTests." + Guid.NewGuid().ToString("N");
    private readonly TaskCompletionSource<nint> created = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly Thread windowThread;
    private bool disposed;
    private static string? paintFailure;
    private static readonly NativeWindowApi.WindowProcedure WindowProcedure = WindowProc;

    public nint Hwnd { get; }

    public SelfTestWindow()
    {
        windowThread = new Thread(WindowThreadMain)
        {
            IsBackground = true,
            Name = "StarMap.NativeTests owned-window message loop",
        };
        windowThread.SetApartmentState(ApartmentState.STA);
        windowThread.Start();
        if (!created.Task.Wait(TimeSpan.FromSeconds(10)))
            throw new TimeoutException("The owned test window did not finish native creation.");
        Hwnd = created.Task.GetAwaiter().GetResult();
        TryActivateOwnWindow();
    }

    public bool TryActivateOwnWindow()
    {
        NativeWindowApi.ShowWindow(Hwnd, NativeWindowApi.SwShow);
        NativeWindowApi.BringWindowToTop(Hwnd);
        NativeWindowApi.SetForegroundWindow(Hwnd);
        return NativeWindowApi.GetForegroundWindow() == Hwnd;
    }

    public void MoveOwnWindow()
    {
        if (!NativeWindowApi.GetWindowRect(Hwnd, out var bounds))
            throw NativeWindowApi.NativeFailure("GetWindowRect(own test window)");
        if (!NativeWindowApi.SetWindowPos(Hwnd, nint.Zero, bounds.Left + 54, bounds.Top + 37,
                0, 0, NativeWindowApi.SwpNoSize | NativeWindowApi.SwpNoZOrder | NativeWindowApi.SwpNoActivate))
            throw NativeWindowApi.NativeFailure("SetWindowPos(own test window)");
        NativeWindowApi.InvalidateRect(Hwnd, nint.Zero, erase: true);
        NativeWindowApi.UpdateWindow(Hwnd);
        CheckPaintSucceeded();
    }

    private void WindowThreadMain()
    {
        var instance = NativeWindowApi.GetModuleHandle(null);
        nint hwnd = nint.Zero;
        var registered = false;
        try
        {
            var windowClass = new NativeWindowApi.WindowClassEx
            {
                Size = checked((uint)Marshal.SizeOf<NativeWindowApi.WindowClassEx>()),
                Style = NativeWindowApi.CsHRedraw | NativeWindowApi.CsVRedraw,
                Procedure = WindowProcedure,
                Instance = instance,
                ClassName = className,
            };
            if (NativeWindowApi.RegisterClassEx(ref windowClass) == 0)
                throw NativeWindowApi.NativeFailure("RegisterClassExW");
            registered = true;

            hwnd = NativeWindowApi.CreateWindowEx(
                0,
                className,
                "StarMap owned native capture color chart",
                NativeWindowApi.WsOverlappedWindow,
                120,
                110,
                940,
                680,
                nint.Zero,
                nint.Zero,
                instance,
                nint.Zero);
            if (hwnd == nint.Zero)
                throw NativeWindowApi.NativeFailure("CreateWindowExW");

            NativeWindowApi.ShowWindow(hwnd, NativeWindowApi.SwShow);
            NativeWindowApi.InvalidateRect(hwnd, nint.Zero, erase: true);
            NativeWindowApi.UpdateWindow(hwnd);
            CheckPaintSucceeded();
            created.TrySetResult(hwnd);

            while (true)
            {
                var status = NativeWindowApi.GetMessage(out var message, nint.Zero, 0, 0);
                if (status == 0) break;
                if (status < 0) throw NativeWindowApi.NativeFailure("GetMessageW(owned test window)");
                NativeWindowApi.TranslateMessage(ref message);
                NativeWindowApi.DispatchMessage(ref message);
            }
        }
        catch (Exception error)
        {
            created.TrySetException(error);
        }
        finally
        {
            if (hwnd != nint.Zero && NativeWindowApi.IsWindow(hwnd)
                && !NativeWindowApi.DestroyWindow(hwnd))
                Interlocked.CompareExchange(ref paintFailure,
                    NativeWindowApi.NativeFailure("DestroyWindow(owned test window)").ToString(), null);
            if (registered && !NativeWindowApi.UnregisterClass(className, instance))
                Interlocked.CompareExchange(ref paintFailure,
                    NativeWindowApi.NativeFailure("UnregisterClassW(owned test window)").ToString(), null);
        }
    }

    private static nint WindowProc(nint hwnd, uint message, nuint wParam, nint lParam)
    {
        try
        {
            if (message == NativeWindowApi.WmEraseBkgnd)
                return (nint)1;
            if (message == NativeWindowApi.WmPaint)
            {
                PaintColorChart(hwnd);
                return nint.Zero;
            }
            if (message == NativeWindowApi.WmDestroy)
            {
                NativeWindowApi.PostQuitMessage(0);
                return nint.Zero;
            }
        }
        catch (Exception error)
        {
            Interlocked.CompareExchange(ref paintFailure, error.ToString(), null);
        }

        return NativeWindowApi.DefWindowProc(hwnd, message, wParam, lParam);
    }

    private static void PaintColorChart(nint hwnd)
    {
        var paint = new NativeWindowApi.PaintStruct { Reserved = new byte[32] };
        var hdc = NativeWindowApi.BeginPaint(hwnd, ref paint);
        if (hdc == nint.Zero)
            throw NativeWindowApi.NativeFailure("BeginPaint(owned test window)");
        try
        {
            if (!NativeWindowApi.GetClientRect(hwnd, out var client))
                throw NativeWindowApi.NativeFailure("GetClientRect(owned test window)");
            var first = client.Right / 3;
            var second = (client.Right * 2) / 3;
            Fill(hwnd, hdc, new NativeWindowApi.Rect { Left = 0, Top = 0, Right = first, Bottom = client.Bottom }, 0x000000FF);
            Fill(hwnd, hdc, new NativeWindowApi.Rect { Left = first, Top = 0, Right = second, Bottom = client.Bottom }, 0x0000FF00);
            Fill(hwnd, hdc, new NativeWindowApi.Rect { Left = second, Top = 0, Right = client.Right, Bottom = client.Bottom }, 0x00FF0000);
        }
        finally
        {
            if (!NativeWindowApi.EndPaint(hwnd, ref paint))
                throw NativeWindowApi.NativeFailure("EndPaint(owned test window)");
        }
    }

    private static void Fill(nint hwnd, nint hdc, NativeWindowApi.Rect rect, uint colorRef)
    {
        var brush = NativeWindowApi.CreateSolidBrush(colorRef);
        if (brush == nint.Zero)
            throw NativeWindowApi.NativeFailure("CreateSolidBrush(test chart)");
        try
        {
            if (NativeWindowApi.FillRect(hdc, ref rect, brush) == 0)
                throw NativeWindowApi.NativeFailure("FillRect(test chart)");
        }
        finally
        {
            if (!NativeWindowApi.DeleteObject(brush))
                throw NativeWindowApi.NativeFailure("DeleteObject(test chart brush)");
        }
    }

    private static void CheckPaintSucceeded()
    {
        if (paintFailure is { } error)
            throw new InvalidOperationException("Owned test window paint failed: " + error);
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (Hwnd != nint.Zero && NativeWindowApi.IsWindow(Hwnd)
            && !NativeWindowApi.PostMessage(Hwnd, NativeWindowApi.WmClose, 0, nint.Zero))
            throw NativeWindowApi.NativeFailure("PostMessageW(WM_CLOSE owned test window)");
        if (!windowThread.Join(TimeSpan.FromSeconds(5)))
            throw new TimeoutException("The owned test window thread did not stop after WM_CLOSE.");
        CheckPaintSucceeded();
    }
}

internal static class ProcessTree
{
    private const uint Th32csSnapProcess = 0x00000002;
    private static readonly nint InvalidHandle = new(-1);

    public static HashSet<uint> SnapshotDescendants(uint root)
    {
        var processes = Enumerate();
        var descendants = new HashSet<uint> { root };
        var changed = true;
        while (changed)
        {
            changed = false;
            foreach (var (processId, parentId) in processes)
                if (descendants.Contains(parentId) && descendants.Add(processId))
                    changed = true;
        }
        return descendants;
    }

    public static HashSet<uint> SnapshotAllProcessIds() => Enumerate().Select(row => row.ProcessId).ToHashSet();

    private static List<(uint ProcessId, uint ParentProcessId)> Enumerate()
    {
        var snapshot = NativeWindowApi.CreateToolhelp32Snapshot(Th32csSnapProcess, 0);
        if (snapshot == InvalidHandle)
            throw NativeWindowApi.NativeFailure("CreateToolhelp32Snapshot");
        try
        {
            var entry = new NativeWindowApi.ProcessEntry32
            {
                Size = checked((uint)Marshal.SizeOf<NativeWindowApi.ProcessEntry32>()),
            };
            if (!NativeWindowApi.Process32First(snapshot, ref entry))
                throw NativeWindowApi.NativeFailure("Process32FirstW");
            var results = new List<(uint, uint)> { (entry.ProcessId, entry.ParentProcessId) };
            while (NativeWindowApi.Process32Next(snapshot, ref entry))
                results.Add((entry.ProcessId, entry.ParentProcessId));
            var lastError = Marshal.GetLastWin32Error();
            if (lastError != 0 && lastError != 18) // ERROR_NO_MORE_FILES
                throw new Win32Exception(lastError, "Process32NextW failed.");
            return results;
        }
        finally
        {
            if (!NativeWindowApi.CloseHandle(snapshot))
                throw NativeWindowApi.NativeFailure("CloseHandle(process snapshot)");
        }
    }
}

internal static class NativeWindowApi
{
    internal const uint CsHRedraw = 0x0002;
    internal const uint CsVRedraw = 0x0001;
    internal const uint WsOverlappedWindow = 0x00CF0000;
    internal const int SwShow = 5;
    internal const uint SwpNoSize = 0x0001;
    internal const uint SwpNoZOrder = 0x0004;
    internal const uint SwpNoActivate = 0x0010;
    internal const uint WmDestroy = 0x0002;
    internal const uint WmClose = 0x0010;
    internal const uint WmPaint = 0x000F;
    internal const uint WmEraseBkgnd = 0x0014;

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    internal delegate nint WindowProcedure(nint hwnd, uint message, nuint wParam, nint lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct WindowClassEx
    {
        internal uint Size;
        internal uint Style;
        [MarshalAs(UnmanagedType.FunctionPtr)] internal WindowProcedure Procedure;
        internal int ClassExtra;
        internal int WindowExtra;
        internal nint Instance;
        internal nint Icon;
        internal nint Cursor;
        internal nint Background;
        [MarshalAs(UnmanagedType.LPWStr)] internal string? MenuName;
        [MarshalAs(UnmanagedType.LPWStr)] internal string ClassName;
        internal nint SmallIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Rect
    {
        internal int Left;
        internal int Top;
        internal int Right;
        internal int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Point
    {
        internal int X;
        internal int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Message
    {
        internal nint Hwnd;
        internal uint Id;
        internal nuint WParam;
        internal nint LParam;
        internal uint Time;
        internal Point Point;
        internal uint Private;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PaintStruct
    {
        internal nint Hdc;
        internal int Erase;
        internal Rect Paint;
        internal int Restore;
        internal int IncUpdate;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)] internal byte[] Reserved;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct ProcessEntry32
    {
        internal uint Size;
        internal uint Usage;
        internal uint ProcessId;
        internal nuint DefaultHeapId;
        internal uint ModuleId;
        internal uint Threads;
        internal uint ParentProcessId;
        internal int BasePriority;
        internal uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] internal string ExecutableFile;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetModuleHandleW", SetLastError = true)]
    internal static extern nint GetModuleHandle(string? moduleName);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "RegisterClassExW", SetLastError = true)]
    internal static extern ushort RegisterClassEx(ref WindowClassEx windowClass);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "CreateWindowExW", SetLastError = true)]
    internal static extern nint CreateWindowEx(uint extendedStyle, string className, string windowName,
        uint style, int x, int y, int width, int height, nint parent, nint menu, nint instance, nint parameter);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool ShowWindow(nint hwnd, int command);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetForegroundWindow(nint hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool BringWindowToTop(nint hwnd);

    [DllImport("user32.dll")]
    internal static extern nint GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsWindow(nint hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DestroyWindow(nint hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "UnregisterClassW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool UnregisterClass(string className, nint instance);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetClientRect(nint hwnd, out Rect rect);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowRect(nint hwnd, out Rect rect);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPos(nint hwnd, nint insertAfter, int x, int y,
        int width, int height, uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool InvalidateRect(nint hwnd, nint rect, bool erase);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool UpdateWindow(nint hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetMessageW", SetLastError = true)]
    internal static extern int GetMessage(out Message message, nint hwnd, uint minimum, uint maximum);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool TranslateMessage(ref Message message);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "DispatchMessageW")]
    internal static extern nint DispatchMessage(ref Message message);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "PostMessageW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool PostMessage(nint hwnd, uint message, nuint wParam, nint lParam);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern nint BeginPaint(nint hwnd, ref PaintStruct paint);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EndPaint(nint hwnd, ref PaintStruct paint);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int FillRect(nint hdc, ref Rect rect, nint brush);

    [DllImport("gdi32.dll", SetLastError = true)]
    internal static extern nint CreateSolidBrush(uint colorRef);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DeleteObject(nint gdiObject);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "DefWindowProcW")]
    internal static extern nint DefWindowProc(nint hwnd, uint message, nuint wParam, nint lParam);

    [DllImport("user32.dll")]
    internal static extern void PostQuitMessage(int exitCode);

    [DllImport("kernel32.dll")]
    internal static extern nint GetCurrentProcess();

    [DllImport("user32.dll")]
    internal static extern uint GetGuiResources(nint process, uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern nint CreateToolhelp32Snapshot(uint flags, uint processId);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32FirstW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool Process32First(nint snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32NextW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool Process32Next(nint snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseHandle(nint handle);

    internal static Exception NativeFailure(string operation)
    {
        var error = Marshal.GetLastWin32Error();
        return error == 0
            ? new InvalidOperationException($"{operation} failed without a Win32 error code.")
            : new Win32Exception(error, $"{operation} failed.");
    }
}
