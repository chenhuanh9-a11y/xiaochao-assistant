using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace StarMap.App;

public partial class WorkspaceWindow
{
    readonly Dictionary<string, WeakReference<IInputElement>> pageFocus = [];
    string? visiblePage;
    Expander? advancedHotkeysDisclosure;
    Expander? homePreparation;
    ScrollViewer? homeScroll;
    VisualStateGroup? connectionExpansion;
    Button? saveSettingsButton, importSettingsButton;
    readonly TextBlock settingsDirtyState = new() { FontWeight = FontWeights.SemiBold, Margin = new(0, 0, 0, 6) };

    void OpenHomeConnection(object sender, RoutedEventArgs args)
    {
        if (homePreparation is null || connectionPage is null) return;
        homePreparation.ApplyTemplate();
        var root = VisualTreeHelper.GetChildrenCount(homePreparation) > 0 ? VisualTreeHelper.GetChild(homePreparation, 0) as FrameworkElement : null;
        var expansion = root is null ? null : VisualStateManager.GetVisualStateGroups(root).OfType<VisualStateGroup>().FirstOrDefault(group => group.Name == "ExpansionStates");
        var scale = homePreparation.Template?.FindName("ContentSiteScaleTransform", homePreparation) as ScaleTransform;
        bool waitForExpansion = expansion is not null && (!homePreparation.IsExpanded || scale is { ScaleY: < 1 });
        CancelConnectionReveal();
        if (waitForExpansion)
        {
            connectionExpansion = expansion;
            connectionExpansion!.CurrentStateChanged += ConnectionExpansionFinished;
        }
        homePreparation.IsExpanded = true;
        if (!waitForExpansion) QueueConnectionReveal();
    }

    void ConnectionExpansionFinished(object? sender, VisualStateChangedEventArgs args)
    {
        CancelConnectionReveal();
        if (args.NewState.Name == "Expanded") QueueConnectionReveal();
    }

    void CancelConnectionReveal()
    {
        if (connectionExpansion is not null) connectionExpansion.CurrentStateChanged -= ConnectionExpansionFinished;
        connectionExpansion = null;
    }

    void QueueConnectionReveal()
    {
        _ = Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() =>
        {
            if (closed || closing || visiblePage != "home" || homePreparation?.IsExpanded != true || connectionPage is null
                || homeScroll?.Content is not FrameworkElement content || !ReferenceEquals(PageContent.Content, homeScroll)) return;
            // MDIX expands through a LayoutTransform. Wait for its visual state,
            // then measure the target in the physical scroll owner's content.
            homeScroll.UpdateLayout();
            var location = connectionPage.TransformToAncestor(content).Transform(new Point());
            homeScroll.ScrollToVerticalOffset(location.Y);
        }));
    }

    void UpdateSettingsToolbar()
    {
        bool available = !settingsBusy && !resourcesBusy;
        if (saveSettingsButton is not null) saveSettingsButton.IsEnabled = available && settings is not null;
        if (importSettingsButton is not null) importSettingsButton.IsEnabled = available;
        settingsDirtyState.Text = settingsBusy ? "正在处理设置…" : settings is null ? "设置尚未载入" : editedSettings.Count > 0 ? "有未保存的更改" : "没有未保存的更改";
        settingsDirtyState.SetResourceReference(TextBlock.ForegroundProperty, editedSettings.Count > 0 ? "StarMap.Accent" : "StarMap.Muted");
    }

    void InitializePageFocus()
    {
        PageContent.PreviewLostKeyboardFocus += (_, args) =>
        {
            if (visiblePage is not null && args.OldFocus is DependencyObject oldFocus && PageContent.IsAncestorOf(oldFocus))
                pageFocus[visiblePage] = new(args.OldFocus);
        };
    }

    void SelectPage(string key)
    {
        var item = Navigation.Items.Cast<ListBoxItem>().FirstOrDefault(row => row.Tag as string == key && row.IsEnabled);
        if (item is not null) Navigation.SelectedItem = item;
    }

    void RememberPageFocus()
    {
        if (visiblePage is not null && PageContent.IsKeyboardFocusWithin && Keyboard.FocusedElement is { } focus)
            pageFocus[visiblePage] = new(focus);
    }

    void RestorePageFocus(string key)
    {
        visiblePage = key;
        if (!pageFocus.TryGetValue(key, out var saved)) return;
        _ = Dispatcher.BeginInvoke(DispatcherPriority.Input, new Action(() =>
        {
            if (!closed && visiblePage == key && saved.TryGetTarget(out var target) && target is UIElement { IsVisible: true, IsEnabled: true, Focusable: true })
                Keyboard.Focus(target);
        }));
    }

    static ScrollViewer PageScroll(FrameworkElement content) => new()
    {
        Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new(0, 0, 8, 2)
    };

    static Border Surface(UIElement child)
    {
        var surface = new Border { Child = child, Padding = new(20), CornerRadius = new(12), BorderThickness = new(1) };
        surface.SetResourceReference(Border.BackgroundProperty, "StarMap.Surface");
        surface.SetResourceReference(Border.BorderBrushProperty, "StarMap.Outline");
        return surface;
    }

    static TextBlock SectionHeading(string text, Thickness margin) => new()
    {
        Text = text, FontSize = 17, FontWeight = FontWeights.SemiBold, Margin = margin
    };

    static TextBlock BodyText(string text, Thickness? margin = null)
    {
        var block = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Margin = margin ?? new(0), LineHeight = 22 };
        block.SetResourceReference(TextBlock.ForegroundProperty, "StarMap.Muted");
        return block;
    }

    static Expander Disclosure(string title, UIElement content, string name) => new()
    {
        Header = title, Content = content, Name = name, IsExpanded = false,
        HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new(0, 4, 0, 4)
    };

    static Button TextButton(string text, RoutedEventHandler action)
    {
        var button = Button(text, action); button.SetResourceReference(StyleProperty, "StarMap.TextButton"); return button;
    }

    static Grid ResponsiveColumns(FrameworkElement first, FrameworkElement second)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new()); grid.ColumnDefinitions.Add(new());
        grid.RowDefinitions.Add(new() { Height = GridLength.Auto }); grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
        first.VerticalAlignment = VerticalAlignment.Top; second.VerticalAlignment = VerticalAlignment.Top;
        grid.Children.Add(first); grid.Children.Add(second);
        bool? sideBySide = null;
        void ArrangeColumns(double width)
        {
            bool wide = width >= 740;
            if (sideBySide == wide) return;
            sideBySide = wide;
            grid.ColumnDefinitions[1].Width = wide ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
            Grid.SetColumn(second, wide ? 1 : 0); Grid.SetRow(second, wide ? 0 : 1);
            first.Margin = wide ? new(0, 0, 8, 0) : new(0);
            second.Margin = wide ? new(8, 0, 0, 0) : new(0, 16, 0, 0);
        }
        ArrangeColumns(0);
        grid.SizeChanged += (_, args) => ArrangeColumns(args.NewSize.Width);
        return grid;
    }
}
