using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;
using StarMap.Core.Settings;

namespace StarMap.App;

internal sealed class CustomGalleryPage : UserControl
{
    readonly ExperimentalDataRoot root;
    readonly ComboBox galleries = new() { DisplayMemberPath = nameof(CustomGallerySummary.Name), MinWidth = 200 };
    readonly ComboBox maps = new() { MinWidth = 120 };
    readonly DataGrid points = new() { IsReadOnly = true, AutoGenerateColumns = false, CanUserAddRows = false, CanUserDeleteRows = false };
    readonly TextBlock state = new() { TextWrapping = TextWrapping.Wrap };
    CustomGalleriesDocument? document;

    internal CustomGalleryPage(ExperimentalDataRoot root)
    {
        this.root = root;
        var panel = new DockPanel();
        var header = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
        header.Children.Add(new TextBlock { Text = "此版本可导入副本并查看路线。绘制、编辑和导出仍待接入。", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 16) });
        var import = new Button { Content = "导入图库副本", HorizontalAlignment = HorizontalAlignment.Left, Padding = new Thickness(15, 8, 15, 8), Margin = new Thickness(0, 0, 0, 14) };
        import.Click += async (_, _) =>
        {
            var dialog = new OpenFileDialog { Title = "选择原版 custom-galleries.json", Filter = "图库文件 (*.json)|*.json" };
            if (dialog.ShowDialog(Window.GetWindow(this)) != true) return;
            import.IsEnabled = false;
            try
            {
                var result = await CustomGalleriesDocument.ImportAsync(root, dialog.FileName);
                if (result.Succeeded && result.Document is not null) SetDocument(result.Document);
                else state.Text = "没有导入：" + result.Error?.Message;
            }
            finally { import.IsEnabled = true; }
        };
        header.Children.Add(import);
        var row = new WrapPanel(); galleries.Margin = new Thickness(0, 0, 12, 10); maps.Margin = new Thickness(0, 0, 0, 10); row.Children.Add(galleries); row.Children.Add(maps); header.Children.Add(row); header.Children.Add(state);
        DockPanel.SetDock(header, Dock.Top); panel.Children.Add(header);
        foreach (var (title, field, width) in new[] { ("楼层", "Floor", 70d), ("X", "X", 90d), ("Y", "Y", 90d), ("类型", "Kind", 100d), ("房间", "Room", 140d), ("颜色", "Color", 100d) })
            points.Columns.Add(new DataGridTextColumn { Header = title, Binding = new System.Windows.Data.Binding(field), Width = width });
        panel.Children.Add(points); Content = panel;
        galleries.SelectionChanged += (_, _) =>
        {
            maps.ItemsSource = (galleries.SelectedItem as CustomGallerySummary)?.MapIds;
            if (maps.Items.Count > 0) maps.SelectedIndex = 0;
            else points.ItemsSource = null;
        };
        maps.SelectionChanged += (_, _) =>
        {
            try { points.ItemsSource = document is not null && galleries.SelectedItem is CustomGallerySummary gallery && maps.SelectedItem is string map ? document.ReadRoute(gallery.Id, map) : null; }
            catch (Exception error) when (error is System.Text.Json.JsonException or ArgumentException or InvalidOperationException) { points.ItemsSource = null; state.Text = "路线无法读取：" + error.Message; }
        };
    }

    internal async Task LoadAsync()
    {
        var result = await CustomGalleriesDocument.LoadAsync(root);
        if (result.Succeeded && result.Document is not null) SetDocument(result.Document);
        else state.Text = "图库未载入：" + result.Error?.Message;
    }
    void SetDocument(CustomGalleriesDocument value)
    {
        document = value; galleries.ItemsSource = value.Galleries;
        state.Text = value.HasFile ? $"已读取 {value.Galleries.Count} 个图库；原版文件保留。" : "测试版中还没有图库，可导入原版副本。";
        if (galleries.Items.Count > 0) galleries.SelectedIndex = 0;
    }
}
