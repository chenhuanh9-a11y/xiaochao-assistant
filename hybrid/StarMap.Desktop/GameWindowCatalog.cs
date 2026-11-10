using System.Runtime.InteropServices;
using System.Text;
using StarMap.Windows;

namespace StarMap.Desktop;

public sealed record GameWindowChoice(nint Hwnd, string Title, uint ProcessId)
{
    public string Label => $"{Title} · PID {ProcessId}";
}

public static class GameWindowCatalog
{
    public static GameWindowChoice[] Read()
    {
        var result = new List<GameWindowChoice>();
        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd)) return true;
            var text = new StringBuilder(512);
            GetWindowText(hwnd, text, text.Capacity);
            var title = text.ToString().Trim();
            // Candidate filtering only. The existing exact title+executable
            // policy remains the acceptance owner in WindowCapture.
            if (title != "第五人格" && !title.StartsWith("identity", StringComparison.OrdinalIgnoreCase)) return true;
            try
            {
                var facts = WindowCapture.Inspect(hwnd);
                if (WindowCapture.IsIdentityV(facts)) result.Add(new(hwnd, facts.Title, facts.Identity.ProcessId));
            }
            catch (Exception error) when (error is System.ComponentModel.Win32Exception or InvalidOperationException) { }
            return true;
        }, 0);
        return result.ToArray();
    }
    private delegate bool EnumWindow(nint hwnd, nint state);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool EnumWindows(EnumWindow callback, nint state);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsWindowVisible(nint hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetWindowTextW")] private static extern int GetWindowText(nint hwnd, StringBuilder text, int maximum);
}
