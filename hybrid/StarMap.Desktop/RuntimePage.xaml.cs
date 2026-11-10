using System.Windows;
using System.Windows.Controls;

namespace StarMap.Desktop;

public partial class RuntimePage : UserControl
{
    public RuntimePage() => InitializeComponent();
    private RuntimeViewModel Model => (RuntimeViewModel)DataContext;
    public event EventHandler? OpenFrameRequested;
    public event EventHandler? RefreshWindowsRequested;
    public event EventHandler? BindWindowRequested;
    private void OnRefreshWindows(object sender, RoutedEventArgs e) => RefreshWindowsRequested?.Invoke(this, EventArgs.Empty);
    private void OnBindWindow(object sender, RoutedEventArgs e) => BindWindowRequested?.Invoke(this, EventArgs.Empty);
    private void OnOpenFrame(object sender, RoutedEventArgs e) => OpenFrameRequested?.Invoke(this, EventArgs.Empty);
    private async void OnStart(object sender, RoutedEventArgs e) => await Model.StartRoundAsync();
    private async void OnPrepare(object sender, RoutedEventArgs e) => await Model.PrepareAsync();
    private async void OnCapture(object sender, RoutedEventArgs e) => await Model.CaptureAsync();
    private async void OnEnd(object sender, RoutedEventArgs e) => await Model.EndRoundAsync();
    private async void OnCancel(object sender, RoutedEventArgs e) => await Model.CancelAsync();
    private async void OnConfirm(object sender, RoutedEventArgs e) => await Model.ConfirmAsync();
    private async void OnSetFloor(object sender, RoutedEventArgs e) => await Model.SetFloorAsync();
    private async void OnToggleSheet(object sender, RoutedEventArgs e) => await Model.ToggleSheetAsync();
    private async void OnSelectCandidate(object sender, RoutedEventArgs e) => await Model.SelectCandidateAsync();
}
