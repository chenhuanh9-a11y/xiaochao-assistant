// The baked policy admits fresh targets and binds all later writes to ownership.
var nativeUpgradeFiles = __UPGRADE_FILES__;

function nativeHelperResult(result) {
    if (result.length !== 2) throw new Error("无法启动安装支持工具。");
    try { return JSON.parse(result[0]); }
    catch (error) { throw new Error("安装支持工具未返回完整结果，请保留现场。" + result[0]); }
}

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
    console.log("TRIAL_UI_LANGUAGE: " + installer.value("UILanguage"));
    var scope = __SCOPE__;
    installer.setValue("TrialNativeUpgradeFiles", JSON.stringify(nativeUpgradeFiles));
    var portable = scope === "";
    installer.setValue("TrialPortable", portable ? "true" : "false");
    if (!portable && (!installer.fileExists(scope + "/.qtifw-trial-scope") ||
        installer.readFile(scope + "/.qtifw-trial-scope", "UTF-8").trim() !== __INSTANCE__))
        throw new Error("本地测试范围不匹配，未接管安装目录。");
    var upgradeSupport = installer.environmentVariable("XINGTU_QTIFW_UPGRADE_SUPPORT");
    var upgradeInvocation = !!upgradeSupport;
    // The data component owns the installed support tree. Maintenance must
    // read that current tree, not an old installer's additional QRC archive.
    var installedSupport = portable && !installer.isInstaller() && !upgradeInvocation;
    installer.setValue("TrialUpgradeActive", upgradeInvocation ? "true" : "false");
    var temporary = installer.environmentVariable("TEMP") || installer.environmentVariable("TMP");
    if (portable && !temporary) throw new Error("无法获取系统临时目录。");
    var support = upgradeInvocation ? upgradeSupport : installedSupport ?
        installer.value("TargetDir") + "/_installer-support" : portable ? temporary +
        "/xingtu-setup-" + __INSTANCE__ + "-" + String(Date.now()) + "-" + String(Math.random()).substring(2) :
        scope + "/session-tools/" + String(Date.now());
    if (!installedSupport && !installer.performOperation("Mkdir", [support])) throw new Error("无法准备测试工具目录");
    var helper = support + "/qtifw-trial-helper.exe";
    var supportFiles = __SUPPORT_FILES__;
    for (var i = 0; !upgradeInvocation && !installedSupport && i < supportFiles.length; ++i) {
        var relative = supportFiles[i];
        var slash = relative.lastIndexOf("/");
        if (slash >= 0 && !installer.performOperation("Mkdir", [support + "/" + relative.substring(0, slash)]))
            throw new Error("无法准备安装支持目录");
        if (!installer.performOperation("Copy", [":/qtifw-trial/" + relative, support + "/" + relative]))
            throw new Error("无法读取随包安装工具");
    }
    // QRC copies inherit read-only permissions on Windows. Normalize this
    // private staging tree before native Copy records permanent support files;
    // otherwise Copy UNDO cannot delete them during a failed installation.
    if (portable && !upgradeInvocation && !installedSupport) {
        var attrib = installer.environmentVariable("SystemRoot") + "/System32/attrib.exe";
        var writable = installer.execute(attrib, ["-R", installer.toNativeSeparators(support + "/*"), "/S"]);
        if (writable.length !== 2 || writable[1] !== 0)
            throw new Error("无法准备可写的安装支持文件。");
    }
    installer.setValue("TrialSupport", support);
    installer.setValue("TrialHelper", helper);
    if (installedSupport && !installer.fileExists(helper))
        throw new Error("安装维护文件不完整，请使用完整 Setup 修复。");
    if (installer.environmentVariable("XINGTU_QTIFW_VERIFY_MAINTENANCE") === "1") {
        // Read the persisted controller/helper while the Setup helper still owns
        // the runtime lease. A stale maintenance controller cannot pass this.
        trialRun("verify-program");
        var maintenancePolicy = JSON.parse(installer.readFile(support + "/_internal/qtifw-trial-policy.json", "UTF-8"));
        if (maintenancePolicy.upgradeCapable) {
            var hive = installer.value("AllUsers", "false") === "true" ? "HKEY_LOCAL_MACHINE" : "HKEY_CURRENT_USER";
            var registration = hive + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + installer.value("ProductUUID");
            if (installer.value(registration + "\\DisplayVersion", "", QSettings.NativeFormat) !== maintenancePolicy.targetVersion.version)
                throw new Error("Windows 安装版本登记尚未完成，请重新运行同一个 Setup。");
        }
        console.log("XINGTU_MAINTENANCE_SOURCE_VERIFIED: " + __INSTANCE__);
        return;
    }
    if (upgradeInvocation) {
        var base = installer.environmentVariable("XINGTU_QTIFW_UPGRADE_BASE");
        var replacement = installer.environmentVariable("XINGTU_QTIFW_UPGRADE_RCC");
        if (!portable || !base || !replacement || !installer.fileExists(helper) ||
                !installer.fileExists(base) || !installer.fileExists(replacement))
            throw new Error("更新支持文件不完整，请重新运行完整 Setup。");
        // CLI update mode is selected after construction. Do not use
        // isUpdater() here. QtIFW consumes this disposable base on success.
        installer.setInstallerBaseBinary(base);
        installer.setValue("DefaultResourceReplacement", replacement);
        var upgradePolicy = JSON.parse(installer.readFile(support + "/_internal/qtifw-trial-policy.json", "UTF-8"));
        installer.setValue("ProductVersion", upgradePolicy.targetVersion.version);
    }
    // The normal maintenance executable is an uninstaller. The complete Setup
    // supplies an explicit updater controller for native component updates.
    if (!installer.isInstaller() && !installer.isCommandLineInstance() && !upgradeInvocation) installer.setUninstaller();
    // The introduction owns QtIFW's metadata fetch. Keep its lifecycle, then
    // advance automatically; hiding it bypasses component/UI loading.
    installer.setDefaultPageVisible(QInstaller.Introduction, true);
    installer.setDefaultPageVisible(QInstaller.ComponentSelection, false);
    installer.setDefaultPageVisible(QInstaller.LicenseCheck, false);
    installer.setDefaultPageVisible(QInstaller.StartMenuSelection, false);
    // QtIFW applies --root AFTER construction. Custom key=value parameters are
    // already available here, so bind the actual target before admission.
    var requested = installer.value("TrialTarget", "");
    var local = installer.environmentVariable("LOCALAPPDATA");
    var recommended = (local || installer.value("HomeDir") + "/AppData/Local") + "/Programs/星图试用版";
    installer.setValue("TrialRecommendedTarget", recommended);
    if (installer.isInstaller()) {
        if (requested !== "") installer.setValue("TargetDir", requested);
        else if (!installer.isCommandLineInstance()) installer.setValue("TargetDir", recommended);
    }
    if (portable && installer.isInstaller() && nativeUpgradeFiles.length > 0) {
        var discovery;
        if (requested !== "") {
            var explicitCheck = installer.execute(helper, ["bootstrap-check", installer.value("TargetDir")]);
            discovery = nativeHelperResult(explicitCheck);
            if (explicitCheck[1] !== 0 && explicitCheck[1] !== 10)
                throw new Error("指定安装位置不可用：" + (discovery.reason || "未知原因"));
        } else {
            var found = installer.execute(helper, ["discover-installation"]);
            discovery = nativeHelperResult(found);
            if (found[1] !== 0) throw new Error("无法检查已有安装：" + (discovery.reason || "未知原因"));
        }
        installer.setValue("TrialInstallDiscovery", JSON.stringify(discovery));
        if (discovery.mode === "update") {
            installer.setValue("TargetDir", discovery.target);
            installer.setValue("TrialNativeHandoff", "true");
            installer.setValue("TrialHandoffTarget", discovery.target);
            // Keep the native route alive until metadata has loaded and the
            // component inserts ExistingUpdate. That page then hides the
            // native transaction pages. Hiding every terminal page here makes
            // Introduction final before component.loaded can create the page.
        }
    }
    if (installer.value("DataChoice", "") === "") installer.setValue("DataChoice", "clear");
    if (installer.isUninstaller() && installer.value("DataChoice") !== "clear")
        throw new Error("此版本执行彻底卸载，不支持保留安装目录。请先备份需要保留的资料。");
    if (installer.value("DataChoice") !== "keep" && installer.value("DataChoice") !== "clear")
        throw new Error("请选择保留或清除测试资料");
    if (installer.isInstaller()) {
        // GUI selection is read-only until the real component's pre-extract
        // admission. Keep early admission for the existing CLI/root contract.
        if (installer.isCommandLineInstance() && installer.value("TrialNativeHandoff") !== "true") {
            trialRun("prepare");
            installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
            installer.setMessageBoxAutomaticAnswer("OverwriteTargetDirectory", QMessageBox.Yes);
        }
        installer.setDefaultPageVisible(QInstaller.TargetDirectory, false);
        installer.setDefaultPageVisible(QInstaller.StartMenuSelection, false);
    } else if (!upgradeInvocation) {
        // A signal callback exception does not stop QtIFW. Constructor failure
        // happens before native operations, including for command-line purge.
        trialRun(installer.isUninstaller() ? "hold-uninstall" : "inspect-data");
    }
    // Disarm a stale prior choice on every maintenance/update invocation.
    if (!installer.isInstaller() || installer.isCommandLineInstance())
        trialRun("select-data", "keep", "none");
    var admittedTarget = installer.value("TargetDir");
    function normal(path) { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
    installer.valueChanged.connect(function(key, value) {
        var bound = installer.isInstaller() ? installer.value("TrialHandoffTarget", "") ||
            installer.value("TrialPreparedTarget", "") : admittedTarget;
        if (key === "TargetDir" && bound !== "" && normal(value) !== normal(bound)) {
            installer.setValue("TrialRootRejected", "true");
            installer.setValue("TargetDir", bound);
        }
    });
    installer.installationFinished.connect(function() {
        if (upgradeInvocation) return; // Setup helper verifies metadata before adopting the new owner.
        try { trialRun("verify"); }
        catch (error) {
            installer.setValue("TrialVerificationFailed", "true");
            if (installer.status === QInstaller.Success) installer.setCanceled();
        }
    });
    installer.uninstallationStarted.connect(function() {
        trialRun("select-data", installer.value("DataChoice", "keep"), "purge");
    });
}

Controller.prototype.IntroductionPageCallback = function() {
    var page = gui.currentPageWidget();
    if (page && page.MessageLabel)
        page.MessageLabel.text = installer.isInstaller() ?
            "欢迎使用星图安装器试用向导。点击“下一步”开始安装。" :
            "欢迎使用星图维护向导。可在这里维护或卸载本测试安装。";
    gui.clickButton(buttons.NextButton);
};

Controller.prototype.DynamicInstallLocationCallback = function() {
    var back = gui.findChild(gui, "__qt__passive_wizardbutton0");
    if (back) back.visible = false;
};

Controller.prototype.DynamicExistingUpdateCallback = function() {
    // This handoff page owns its update/retry/close actions. The standalone
    // wizard's Next/Finish/Cancel must not start a fresh transaction or show
    // an installation-cancel prompt after a successfully completed update.
    for (var n = 0; n <= 4; ++n) {
        var nativeButton = gui.findChild(gui, "__qt__passive_wizardbutton" + n);
        if (nativeButton) nativeButton.visible = false;
    }
};

Controller.prototype.ReadyForInstallationPageCallback = function() {
    if (installer.isCommandLineInstance()) return;
    if (installer.value("TrialUpgradeActive", "false") === "true") {
        gui.clickButton(buttons.CommitButton);
        return;
    }
    var page = gui.currentPageWidget();
    if (installer.isInstaller()) {
        var back = gui.findChild(gui, "__qt__passive_wizardbutton0");
        if (back) back.visible = true;
        page.title = "确认安装星图";
        page.subTitle = "安装位置：" + installer.toNativeSeparators(installer.value("TargetDir"));
        return;
    }
    if (!installer.isUninstaller()) return;
    var back = gui.findChild(gui, "__qt__passive_wizardbutton0");
    if (back) back.visible = false;
    page.title = "彻底卸载星图";
    page.subTitle = "点击“彻底卸载”后开始移除。";
    var summary = page ? gui.findChild(page, "RemoveAllMsgLabel") : null;
    if (summary)
        summary.text = "将删除以下安装目录及其中的程序、地图、设置、缓存和日志：\n\n" +
            installer.toNativeSeparators(installer.value("TargetDir")) +
            "\n\n同时移除桌面、开始菜单入口和卸载项。安装目录外的文件保留。";
    gui.setWizardPageButtonText(QInstaller.ReadyForInstallation, buttons.CommitButton, "彻底卸载");
};

Controller.prototype.FinishedPageCallback = function() {
    if (installer.value("TrialUpgradeActive", "false") === "true") {
        gui.clickButton(buttons.FinishButton);
        return;
    }
    if (installer.isInstaller()) {
        var installed = false;
        try {
            var checked = JSON.parse(installer.readFile(installer.value("TargetDir") + "/trial-verify.json", "UTF-8"));
            installed = checked.success === true && checked.verified === true &&
                installer.value("TrialVerificationFailed", "") !== "true";
        } catch (error) {}
        var finished = gui.currentPageWidget();
        if (finished && finished.MessageLabel && !installed)
            finished.MessageLabel.text = "安装未通过完整性校验，请保留现场并重新下载安装包。";
        if (finished && finished.MessageLabel && installed)
            finished.MessageLabel.text = "星图试用版已安装。\n\n安装位置：" +
                installer.toNativeSeparators(installer.value("TargetDir")) +
                "\n\n可从桌面“星图（试用版）”或开始菜单“星图试用版”打开程序。" +
                "\n卸载请使用开始菜单中的“卸载星图试用版”。";
        return;
    }
    if (!installer.isUninstaller()) return;
    var text = "卸载流程已结束，正在确认清理结果。";
    try {
        var outcome = installer.value("TrialPortable") === "true" ?
            installer.value("TargetDir") + "/trial-data.json" : __SCOPE__ + "/last-uninstall.json";
        var result = JSON.parse(installer.readFile(outcome, "UTF-8"));
        if (installer.value("TrialPortable") === "true") result.target = installer.value("TargetDir");
        function normal(path) { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
        if (result.success === true && result.dataChoice === "clear" && result.nativeRemovalScheduled === true &&
                normal(result.target) === normal(installer.value("TargetDir")))
            text = "程序移除已完成，安装目录的最终清理已安排。请关闭此窗口后确认目录已移除。";
        else text = "程序卸载流程已结束，但资料处理未完成。请保留测试目录并检查日志。";
    } catch (error) {}
    var page = gui.currentPageWidget();
    if (page && page.MessageLabel) page.MessageLabel.text = text;
    console.log("TRIAL_DATA_OUTCOME: " + text);
};
