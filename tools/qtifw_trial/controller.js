// All mutations are confined by the helper's baked local trial policy.
function trialRun(action, choice) {
    var args = [action, installer.value("TargetDir")];
    if (choice) args.push(choice);
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
    if (installer.value("DataChoice", "") === "") installer.setValue("DataChoice", "keep");
    if (installer.isInstaller()) {
        trialRun("prepare");
        installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
        // Only after the native helper has admitted this owned, isolated target.
        installer.setMessageBoxAutomaticAnswer("OverwriteTargetDirectory", QMessageBox.Yes);
        installer.setDefaultPageVisible(QInstaller.TargetDirectory, false);
    }
    installer.installationFinished.connect(function() { trialRun("verify"); });
    installer.uninstallationStarted.connect(function() {
        if (installer.value("DataChoice") !== "keep" && installer.value("DataChoice") !== "clear")
            throw new Error("请选择保留或清除测试资料");
        trialRun("inspect-data");
    });
    installer.uninstallationFinished.connect(function() {
        trialRun("data", installer.value("DataChoice", "keep"));
    });
}
