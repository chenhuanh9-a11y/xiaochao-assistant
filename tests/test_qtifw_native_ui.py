"""Execute the production scripts in Qt's JS engine with a narrow UI/process boundary."""
import json
from pathlib import Path
import unittest

from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QJSEngine

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])

MOCK = r'''
var console={log:function(){}};
var calls=[], values={TargetDir:"E:/default", HomeDir:"C:/Users/fixture", InstallerFilePath:"E:/fixture/Setup.exe"};
function signal(){return {connect:function(){}};}
function label(){return {text:"",clicked:signal(),textChanged:signal()};}
var location={InstallPath:label(), BrowseButton:label(), FinalPath:label(),LocationError:label(),LocationHelp:label()};
var update={ExistingPath:label(),UpdateButton:label(),CloseButton:label(),UpdateStatus:label()};
var QInstaller={Introduction:0,TargetDirectory:1,ComponentSelection:2,LicenseCheck:3,StartMenuSelection:4,
ReadyForInstallation:5,PerformInstallation:6,InstallationFinished:7,Success:0};
var buttons={NextButton:1,CommitButton:3,FinishButton:4}; var QMessageBox={Yes:1,No:0};var QSettings={NativeFormat:0};
var installer={
value:function(k,d){return values[k]===undefined?(d===undefined?"":d):values[k];},
setValue:function(k,v){values[k]=v;},
isInstaller:function(){return true;},isCommandLineInstance:function(){return false;},isUninstaller:function(){return false;},
environmentVariable:function(k){return k==="TEMP"?"E:/fixture/temp":k==="SystemRoot"?"C:/Windows":"";},
fileExists:function(){return true;},performOperation:function(){calls.push(["operation"].concat(Array.prototype.slice.call(arguments)));return true;},
execute:function(p,a){calls.push(["execute",a]);if(a[0]==="discover-installation")return[JSON.stringify(discovery),0];
if(a[0]==="bootstrap-run")return[JSON.stringify(outcome),outcome.success?0:2];return["{}",0];},
setDefaultPageVisible:function(p,v){calls.push(["page",p,v]);},setMessageBoxAutomaticAnswer:function(){},
toNativeSeparators:function(p){return p;},addWizardPage:function(c,n,p){calls.push(["custom",n,p]);return true;},
setValidatorForCustomPage:function(c,n,v){calls.push(["validator",n,v]);},
valueChanged:signal(),installationFinished:signal(),uninstallationStarted:signal()
};
var component={loaded:signal(),createOperations:function(){calls.push(["native-fresh-operation"]);},addOperation:function(){}};
var gui={pageWidgetByObjectName:function(n){return n==="DynamicExistingUpdate"?(values.TrialNativeHandoff==="true"?update:null):location;},
findChild:function(){return{};},rejectWithoutPrompt:function(){calls.push(["close-handoff"]);},clickButton:function(){}};
'''


class NativeUiTests(unittest.TestCase):
    def scripts(self, discovery, *, outcome=None):
        engine = QJSEngine()
        prefix = "var discovery=" + json.dumps(discovery) + ";var outcome=" + json.dumps(outcome or dict(success=True)) + ";"
        controller = (ROOT / "tools/qtifw_trial/controller.js").read_text(encoding="utf-8")
        controller = controller.replace("__SCOPE__", '""').replace("__INSTANCE__", '"fixture"')
        controller = controller.replace("__SUPPORT_FILES__", '["qtifw-trial-helper.exe"]')
        controller = controller.replace("__UPGRADE_FILES__", '["boot/devtool.exe"]')
        component = (ROOT / "tools/qtifw_trial/component.js").read_text(encoding="utf-8").replace("__PROGRAM_COPIES__", "[]")
        # Controller and component use separate QtIFW script contexts. Shadow
        # controller globals so the fixture cannot accidentally supply them.
        value = engine.evaluate(prefix + MOCK + controller + "\nnew Controller();var instance;" +
            "(function(){var nativeUpgradeFiles;var nativeHelperResult;" + component +
            "\ninstance=new Component();instance.installLocationLoaded();})();")
        self.assertFalse(value.isError(), value.toString())
        return engine

    def value(self, engine, expression):
        value = engine.evaluate("JSON.stringify(" + expression + ")")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def test_verified_old_root_skips_directory_page_and_never_prepares_fresh(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"))
        self.assertEqual(self.value(engine, "values.TargetDir"), "D:/fixture/old")
        customs = [call[1] for call in self.value(engine, "calls") if call[0] == "custom"]
        self.assertEqual(customs, ["ExistingUpdate"])
        calls = self.value(engine, "calls")
        inserted = calls.index(["custom", "ExistingUpdate", 1])
        self.assertTrue(all(index > inserted for index, row in enumerate(calls)
                            if row[0] == "page" and row[1] in (5, 6, 7) and row[2] is False))
        self.assertFalse(any(call[0] == "execute" and call[1][0] == "prepare" for call in self.value(engine, "calls")))
        rejected = engine.evaluate("instance.createOperations()")
        self.assertTrue(rejected.isError())
        self.assertTrue(engine.evaluate("instance.beginInstallation()").isError())
        self.assertNotIn(["native-fresh-operation"], self.value(engine, "calls"))

    def test_fresh_machine_has_directory_choice(self):
        engine = self.scripts(dict(mode="fresh", candidates=[], rejected=[]))
        self.assertIn(["custom", "InstallLocation", 1], self.value(engine, "calls"))

    def test_ambiguous_verified_roots_show_selection_reason(self):
        engine = self.scripts(dict(mode="select", reason="multiple-valid-installations",
                                  candidates=[dict(target="D:/one"), dict(target="E:/two")], rejected=[]))
        help = self.value(engine, "location.LocationHelp.text")
        self.assertIn("多个有效安装", help)
        self.assertIn("E:/two", help)

    def test_update_success_never_enters_native_fresh_and_has_no_cancel_error_copy(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"))
        result = engine.evaluate("instance.updateExisting()")
        self.assertFalse(result.isError(), result.toString())
        self.assertFalse(result.toBool())
        self.assertEqual(self.value(engine, "values.TrialHandoffSuccess"), "true")
        text = self.value(engine, "update.UpdateStatus.text")
        self.assertIn("已更新", text)
        self.assertNotIn("取消", text)
        self.assertNotIn(["native-fresh-operation"], self.value(engine, "calls"))

    def test_update_failure_remains_retryable_and_never_claims_success(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"),
                              outcome=dict(success=False, reason="native-update-failed:2"))
        result = engine.evaluate("instance.updateExisting()")
        self.assertFalse(result.isError(), result.toString())
        self.assertFalse(result.toBool())
        self.assertNotEqual(self.value(engine, "values.TrialHandoffSuccess || ''"), "true")
        self.assertIn("更新未完成", self.value(engine, "update.UpdateStatus.text"))
        self.assertTrue(self.value(engine, "update.UpdateButton.enabled"))

    def test_copy_failure_restores_retry_state_and_cannot_reach_fresh_operations(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"))
        result = engine.evaluate("installer.performOperation=function(){return false;}; instance.updateExisting()")
        self.assertFalse(result.isError(), result.toString())
        self.assertFalse(result.toBool())
        self.assertTrue(self.value(engine, "update.UpdateButton.enabled"))
        self.assertIn("更新未完成", self.value(engine, "update.UpdateStatus.text"))
        self.assertNotIn(["native-fresh-operation"], self.value(engine, "calls"))

    def test_invalid_helper_json_restores_retry_state(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"))
        result = engine.evaluate('installer.execute=function(p,a){return a[0]==="bootstrap-run"?["broken-json",0]:["{}",0];};instance.updateExisting()')
        self.assertFalse(result.isError(), result.toString())
        self.assertTrue(self.value(engine, "update.UpdateButton.enabled"))
        self.assertIn("未返回完整结果", self.value(engine, "update.UpdateStatus.text"))

    def test_successful_update_is_not_executed_twice(self):
        engine = self.scripts(dict(mode="update", target="D:/fixture/old"))
        value = engine.evaluate("instance.updateExisting();instance.updateExisting();")
        self.assertFalse(value.isError(), value.toString())
        runs = [row for row in self.value(engine, "calls") if row[0] == "execute" and row[1][0] == "bootstrap-run"]
        self.assertEqual(len(runs), 1)
        self.assertTrue(self.value(engine, "update.CloseButton.enabled"))

    def test_manual_old_root_validation_navigates_to_same_page_without_starting_update(self):
        engine = self.scripts(dict(mode="select", reason="multiple-valid-installations", candidates=[], rejected=[]))
        value = engine.evaluate('installer.execute=function(p,a){calls.push(["execute",a]);return a[0]==="bootstrap-check"?["{}",10]:["{}",0];};instance.validateLocation()')
        self.assertFalse(value.isError(), value.toString())
        self.assertTrue(value.toBool())
        self.assertIn(["custom", "ExistingUpdate", 5], self.value(engine, "calls"))
        self.assertFalse(any(row[0] == "execute" and row[1][0] == "bootstrap-run" for row in self.value(engine, "calls")))


if __name__ == "__main__":
    unittest.main()
