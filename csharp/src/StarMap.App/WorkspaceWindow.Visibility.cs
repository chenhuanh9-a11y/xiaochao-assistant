using System.IO;
using System.Text.Json.Nodes;
using StarMap.Core.Settings;

namespace StarMap.App;

public partial class WorkspaceWindow
{
    // F11 and the page button share the original immediate visibility path.
    // It deliberately bypasses SessionService.Dispatch: that lane supersedes
    // capture/fit, whereas a presentation preference must leave it running.
    Task ToggleAlignedVisibility()
    {
        if (closed || closing) return Task.CompletedTask;
        if (!visibilityReady) { Status.Text = "设置正在载入，请稍后再切换路线。"; return Task.CompletedTask; }
        bool visible = !(runtimePage?.AlignedVisible ?? settings?.SheetVisibilityEnabled ?? true);
        var document = settings;
        document?.Set("sheetVisibilityEnabled", JsonValue.Create(visible));
        runtimePage?.SetAlignedVisibility(visible);
        runtimeOverlay?.RefreshVisibility();
        Status.Text = visible ? "路线已显示。" : "路线已隐藏，识别仍会继续。";
        if (document is null) { Status.Text += "设置未载入，偏好没有保存。"; return Task.CompletedTask; }
        MarkEdited("sheetVisibilityEnabled");
        // Match saveFields: busy saves do not queue another preference write.
        // The immediate state and its dirty draft remain available to save later.
        if (settingsBusy) return Task.CompletedTask;
        settingsBusy = true; UpdateSettingsToolbar();
        return visibilitySave = SaveVisibility(document, visible);
    }

    async Task SaveVisibility(SettingsDocument document, bool visible)
    {
        try
        {
            var saved = await document.SaveFieldsAsync(dataRoot, ["sheetVisibilityEnabled"]);
            if (closed || !ReferenceEquals(settings, document)) return;
            bool stillCurrent = document.SheetVisibilityEnabled == visible;
            if (saved.Succeeded && stillCurrent)
            {
                editedSettings.Remove("sheetVisibilityEnabled");
                settingsState.Text = editedSettings.Count == 0 ? "显隐偏好已保存。" : "显隐偏好已保存；其它更改尚未保存。";
            }
            else if (saved.Error is not null && stillCurrent)
                settingsState.Text = "显隐已生效，但偏好未保存：" + saved.Error.Message;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException)
        {
            if (!closed && ReferenceEquals(settings, document)) settingsState.Text = "显隐已生效，但偏好未保存：" + error.Message;
        }
        finally
        {
            settingsBusy = false;
            if (!closed) UpdateSettingsToolbar();
        }
    }
}
