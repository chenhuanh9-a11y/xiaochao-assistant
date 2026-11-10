using System.IO;
using System.Windows;

namespace StarMap.Desktop;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var window = new MainWindow(StartupOptions.Parse(e.Args));
        MainWindow = window;
        window.Show();
    }
}

public sealed record StartupOptions(string SourceRoot, string Python, string? ResourceRoot)
{
    public static StartupOptions Parse(string[] args)
    {
        string? Value(string name)
        {
            var index = Array.IndexOf(args, name);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        var source = Value("--source-root");
        if (source is null)
        {
            var directory = new DirectoryInfo(AppContext.BaseDirectory);
            while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "hybrid", "python", "worker.py")))
                directory = directory.Parent;
            source = directory?.FullName ?? Environment.CurrentDirectory;
        }
        return new(Path.GetFullPath(source),
            Value("--python") ?? "python", Value("--resource-root"));
    }
}
