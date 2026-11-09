using System.Windows;

namespace StarMap.App;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        string? Argument(string name)
        {
            int index = Array.IndexOf(e.Args, name);
            return index >= 0 && index + 1 < e.Args.Length ? e.Args[index + 1] : null;
        }
        var dataRoot = Argument("--data-root") ?? System.IO.Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StarMap.Migration");
        MainWindow = new WorkspaceWindow(dataRoot, Argument("--resource-root"));
        MainWindow.Show();
    }
}
