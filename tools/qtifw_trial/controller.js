// All mutations are confined by the helper's baked local trial policy.
function trialRun(action, choice, operation) {
    var args = [action, installer.value("TargetDir")];
    if (choice) args.push(choice);
    if (operation) args.push(operation);
    var result = installer.execute(installer.value("TrialHelper"), args);
    console.log("TRIAL_" + action + ": " + result);
    if (result.length !== 2 || result[1] !== 0)
        throw new Error("测试安装操作未完成：" + action + "。已保留现场，请查看本测试目录日志。");
}

function Controller() {
    var scope = __SCOPE__;
    if (!installer.fileExists(scope + "/.qtifw-trial-scope") ||
        installer.readFile(scope + "/.qtifw-trial-scope", "UTF-8").trim() !== __INSTANCE__)
        throw new Error("本地测试范围不匹配，未接管安装目录。");
    var support = scope + "/session-tools/" + String(Date.now());
    if (!installer.performOperation("Mkdir", [support])) throw new Error("无法准备测试工具目录");
    var helper = support + "/qtifw-trial-helper.exe";
    if (!installer.performOperation("Copy", [":/qtifw-trial/helper.exe", helper]))
        throw new Error("无法读取随包测试工具");
    installer.setValue("TrialHelper", helper);
    // QtIFW applies --root AFTER construction. Custom key=value parameters are
    // already available here, so bind the actual target before admission.
    var requested = installer.value("TrialTarget", "");
    if (installer.isInstaller() && requested !== "") installer.setValue("TargetDir", requested);
    if (installer.value("DataChoice", "") === "") installer.setValue("DataChoice", "keep");
    if (installer.value("DataChoice") !== "keep" && installer.value("DataChoice") !== "clear")
        throw new Error("请选择保留或清除测试资料");
    if (installer.isInstaller()) {
        trialRun("prepare");
        installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
        // Only after the native helper has admitted this owned, isolated target.
        installer.setMessageBoxAutomaticAnswer("OverwriteTargetDirectory", QMessageBox.Yes);
        installer.setDefaultPageVisible(QInstaller.TargetDirectory, false);
    } else {
        // A signal callback exception does not stop QtIFW. Constructor failure
        // happens before native operations, including for command-line purge.
        trialRun("inspect-data");
    }
    // Disarm a stale prior choice on every maintenance/update invocation.
    trialRun("select-data", "keep", "none");
    var admittedTarget = installer.value("TargetDir");
    function normal(path) { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
    installer.valueChanged.connect(function(key, value) {
        if (key === "TargetDir" && normal(value) !== normal(admittedTarget)) {
            installer.setValue("TrialRootRejected", "true");
            installer.setValue("TargetDir", admittedTarget);
        }
    });
    installer.installationFinished.connect(function() { trialRun("verify"); });
    installer.uninstallationStarted.connect(function() {
        trialRun("select-data", installer.value("DataChoice", "keep"), "purge");
    });
}

Controller.prototype.FinishedPageCallback = function() {
    if (!installer.isUninstaller()) return;
    var text = "程序卸载流程已结束。资料处理尚未确认，请保留测试目录并检查日志。";
    try {
        var result = JSON.parse(installer.readFile(installer.value("TargetDir") + "/trial-data.json", "UTF-8"));
        if (result.success === true && result.dataChoice === installer.value("DataChoice", "keep"))
            text = result.dataChoice === "clear" ? "程序已卸载，本测试安装的 work 资料已清除；地图、外部文件及协作锁保留。" : "程序已卸载，测试资料已保留。";
        else text = "程序卸载流程已结束，但资料处理未完成。请保留测试目录并检查日志。";
    } catch (error) {}
    var page = gui.currentPageWidget();
    if (page && page.MessageLabel) page.MessageLabel.text = text;
    console.log("TRIAL_DATA_OUTCOME: " + text);
};
