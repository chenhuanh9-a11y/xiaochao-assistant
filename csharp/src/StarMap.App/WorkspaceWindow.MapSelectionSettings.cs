using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using StarMap.Core.Settings;

namespace StarMap.App;

public partial class WorkspaceWindow
{
    readonly ComboBox mapSelectionMode = new()
    {
        Name = "MapSelectionMode",
        ItemsSource = new[]
        {
            new ModeChoice("auto", "自动识别", true),
            new ModeChoice("manual", "手动选图", true)
        },
        DisplayMemberPath = "Label",
        SelectedValuePath = "Key"
    };
    readonly StackPanel mapSelectionModeField = new()
    {
        Name = "MapSelectionModeField",
        Visibility = Visibility.Collapsed
    };
    readonly TextBlock mapSelectionModeStatus = new()
    {
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(0, 8, 0, 0),
        Visibility = Visibility.Collapsed
    };

    bool mapSelectionSettingsEditingEnabled = true;

    bool IsDraftSeasonS0 => string.Equals(season.SelectedItem?.ToString(), "s0", StringComparison.Ordinal);

    void DisplayMapSelectionModeSetting(SettingsDocument document, JsonObject rawValues)
    {
        bool stored = rawValues.TryGetPropertyValue("mapSelectionMode", out var raw);
        string? selected = !stored ? document.MapSelectionMode
            : raw is JsonValue value && value.TryGetValue<string>(out var text) && text is ("auto" or "manual") ? text : null;
        mapSelectionMode.SelectedValue = selected;
        if (selected is not null)
        {
            mapSelectionModeStatus.Text = string.Empty;
            mapSelectionModeStatus.ToolTip = null;
            mapSelectionModeStatus.Visibility = Visibility.Collapsed;
            return;
        }

        var rawText = raw?.ToJsonString() ?? "null";
        mapSelectionModeStatus.Text = $"当前保存值不受支持（原始值：{rawText}）。原值会保留；选择有效选项并保存成功后才会替换。";
        mapSelectionModeStatus.ToolTip = rawText;
        mapSelectionModeStatus.Visibility = Visibility.Visible;
    }

    void MapSelectionModeChanged()
    {
        MarkEdited("mapSelectionMode");
        if (mapSelectionModeStatus.Visibility == Visibility.Visible
            && mapSelectionMode.SelectedValue?.ToString() is ("auto" or "manual"))
            mapSelectionModeStatus.Text = "已选择有效选项；保存成功后会替换原始值。";
    }

    JsonNode? ReadMapSelectionModeControl() => mapSelectionMode.SelectedValue?.ToString() switch
    {
        "auto" => JsonValue.Create("auto"),
        "manual" => JsonValue.Create("manual"),
        _ => throw new InvalidOperationException("请选择有效的 S0 地图选择方式后再保存。")
    };

    void SetMapSelectionModeEditingEnabled(bool enabled)
    {
        mapSelectionSettingsEditingEnabled = enabled;
        UpdateMapSelectionModeAvailability();
    }

    void UpdateMapSelectionModeAvailability()
    {
        bool visible = IsDraftSeasonS0;
        mapSelectionModeField.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        mapSelectionModeField.IsEnabled = visible && mapSelectionSettingsEditingEnabled;
        mapSelectionMode.IsEnabled = visible && mapSelectionSettingsEditingEnabled;
    }
}
