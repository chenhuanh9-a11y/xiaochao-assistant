using System.IO;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using StarMap.App;
using StarMap.Core.Settings;

internal static class MapSelectionSettingsWorkspaceRegression
{
    internal static async Task Run(string output,Action<bool,string> check)
    {
        string root=Path.Combine(output,"selection-settings-default");
        var shell=new WorkspaceWindow(root,null);
        try
        {
            await Invoke(shell,"LoadSettings");
            var choice=Field<ComboBox>(shell,"mapSelectionMode");var season=Field<ComboBox>(shell,"season");
            var field=Field<StackPanel>(shell,"mapSelectionModeField");
            check(choice.SelectedValue?.ToString()=="auto" && field.Visibility==Visibility.Visible && choice.IsEnabled,
                "missing-selection-setting-displays-S0-auto-in-stable-real-combo");
            choice.SelectedValue="manual";await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual" && Dirty(shell).Count==0,
                "real-save-persists-and-reads-back-manual-preference");
            season.SelectedItem="s1";
            check(field.Visibility==Visibility.Collapsed && !choice.IsEnabled && choice.SelectedValue?.ToString()=="manual",
                "S1-draft-hides-and-disables-selection-field-without-changing-its-value");
            Field<TextBox>(shell,"hotkey").Text="H";await Invoke(shell,"SaveSettings");
            check(Read(root)["gameSeason"]!.GetValue<string>()=="s1" && Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual",
                "saving-S1-settings-keeps-stored-S0-manual-preference");
            season.SelectedItem="s0";choice.SelectedValue="auto";await Invoke(shell,"SaveSettings");
            choice.SelectedValue="manual";season.SelectedItem="s1";await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="auto" && choice.SelectedValue?.ToString()=="manual"
                && Dirty(shell).SetEquals(["mapSelectionMode"]),
                "hidden-S0-draft-is-neither-written-nor-erased-while-saving-S1");
            await Invoke(shell,"SaveSettings");
            check(Dirty(shell).SetEquals(["mapSelectionMode"]) && Read(root)["mapSelectionMode"]!.GetValue<string>()=="auto",
                "saving-only-hidden-S0-draft-does-not-write-it");
            season.SelectedItem="s0";await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual" && Dirty(shell).Count==0,
                "returning-to-S0-saves-the-retained-draft");

            choice.SelectedValue="auto";var settings=Field<SettingsDocument>(shell,"settings");
            var gate=Field<SemaphoreSlim>(settings,"_saveGate");await gate.WaitAsync();Task? saving=null;
            try
            {
                saving=Invoke(shell,"SaveSettings");choice.SelectedValue="manual";
                check(!saving.IsCompleted,"selection-save-uses-real-async-file-save-under-held-existing-gate");
            }
            finally {gate.Release();}
            await saving!;
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="auto" && choice.SelectedValue?.ToString()=="manual"
                && Dirty(shell).SetEquals(["mapSelectionMode"]),
                "save-completion-cannot-overwrite-newer-selection-edit-or-clear-its-dirty-state");
            await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual" && Dirty(shell).Count==0,
                "second-real-save-commits-the-newer-selection-edit");

            choice.SelectedValue="auto";
            using(var locked=new FileStream(Path.Combine(root,"ui-settings.json"),FileMode.Open,FileAccess.Read,FileShare.Read))
            {
                await Invoke(shell,"SaveSettings");
                check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual" && Dirty(shell).Contains("mapSelectionMode")
                    && Field<TextBlock>(shell,"settingsState").Text.Contains("没有保存"),
                    "failed-replacement-preserves-stored-preference-and-retryable-selection-draft");
            }
            season.SelectedItem="s1";Field<TextBox>(shell,"hotkey").Text="J";await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="manual" && Read(root)["hotkey"]!.GetValue<string>()=="J"
                && Dirty(shell).SetEquals(["mapSelectionMode"]),
                "failed-S0-save-cannot-leak-its-hidden-draft-through-a-later-S1-save");
            season.SelectedItem="s0";await Invoke(shell,"SaveSettings");
            check(Read(root)["mapSelectionMode"]!.GetValue<string>()=="auto" && Dirty(shell).Count==0,
                "selection-save-recovers-after-file-lock-and-hidden-S1-save-without-losing-its-draft");
        }
        finally {await Field<RuntimePage>(shell,"runtimePage").CloseAsync();shell.Close();}

        JsonNode?[] invalid=[JsonValue.Create("future-selection"),JsonValue.Create(37),new JsonObject {["future"]="kept"},null];
        for(int i=0;i<invalid.Length;i++)
        {
            string badRoot=Path.Combine(output,"selection-settings-invalid-"+i);Directory.CreateDirectory(badRoot);
            var source=new JsonObject {["gameSeason"]="s0",["mapSelectionMode"]=invalid[i]?.DeepClone(),["unknownFuture"]=new JsonArray(1,2)};
            await File.WriteAllTextAsync(Path.Combine(badRoot,"ui-settings.json"),source.ToJsonString());
            var window=new WorkspaceWindow(badRoot,null);
            try
            {
                await Invoke(window,"LoadSettings");var choice=Field<ComboBox>(window,"mapSelectionMode");
                var status=Field<TextBlock>(window,"mapSelectionModeStatus");string raw=invalid[i]?.ToJsonString()??"null";
                check(choice.SelectedValue is null && status.Visibility==Visibility.Visible && status.Text.Contains(raw)
                    && Dirty(window).Count==0,"invalid-selection-"+i+"-shows-actual-raw-value-without-an-implicit-repair");
                Field<TextBox>(window,"hotkey").Text="H";await Invoke(window,"SaveSettings");
                check(JsonNode.DeepEquals(Read(badRoot)["mapSelectionMode"],invalid[i])
                    && JsonNode.DeepEquals(Read(badRoot)["unknownFuture"],source["unknownFuture"])
                    && choice.SelectedValue is null,"unrelated-save-preserves-invalid-selection-"+i+"-and-unknown-settings");
                choice.SelectedValue="manual";
                if(i==0)
                {
                    using(var locked=new FileStream(Path.Combine(badRoot,"ui-settings.json"),FileMode.Open,FileAccess.Read,FileShare.Read))
                    {
                        await Invoke(window,"SaveSettings");
                        check(JsonNode.DeepEquals(Read(badRoot)["mapSelectionMode"],invalid[i]) && Dirty(window).Contains("mapSelectionMode"),
                            "failed-explicit-selection-repair-keeps-malformed-raw-on-disk");
                    }
                    Field<ComboBox>(window,"season").SelectedItem="s1";await Invoke(window,"SaveSettings");
                    check(JsonNode.DeepEquals(Read(badRoot)["mapSelectionMode"],invalid[i]) && Dirty(window).SetEquals(["mapSelectionMode"]),
                        "S1-save-after-failed-repair-cannot-replace-malformed-raw-with-hidden-valid-draft");
                    Field<ComboBox>(window,"season").SelectedItem="s0";
                }
                await Invoke(window,"SaveSettings");
                check(Read(badRoot)["mapSelectionMode"]!.GetValue<string>()=="manual" && status.Visibility==Visibility.Collapsed
                    && Dirty(window).Count==0,"explicit-valid-choice-and-successful-save-repair-selection-"+i);
            }
            finally {await Field<RuntimePage>(window,"runtimePage").CloseAsync();window.Close();}
        }
    }

    static JsonObject Read(string root)=>JsonNode.Parse(File.ReadAllBytes(Path.Combine(root,"ui-settings.json")))!.AsObject();
    static HashSet<string> Dirty(WorkspaceWindow window)=>Field<HashSet<string>>(window,"editedSettings");
    static Task Invoke(object owner,string method)=>(Task)owner.GetType().GetMethod(method,BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(owner,null)!;
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
}
