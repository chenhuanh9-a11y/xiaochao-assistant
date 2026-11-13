function Component() {
    if (installer.isInstaller() && !installer.isCommandLineInstance())
        component.loaded.connect(this, Component.prototype.installLocationLoaded);
}

function nativeUpdateResult(result) {
    if (result.length !== 2) throw new Error("无法启动更新支持工具。");
    try { return JSON.parse(result[0]); }
    catch (error) { throw new Error("更新工具未返回完整结果，请保留现场。" + result[0]); }
}

Component.prototype.beginInstallation = function() {
    // QtIFW calls this before clearing its fresh-install component database.
    // A createOperations-only refusal happens after that destructive boundary.
    if (installer.isInstaller() && installer.value("TrialNativeHandoff", "false") === "true")
        throw new Error("已有安装只允许原维护器更新，未启动新装事务。");
};

function locationProblem(result) {
    var reason = "";
    try { reason = JSON.parse(result[0]).reason || ""; } catch (error) {}
    if (reason === "use-existing-maintenance-tool")
        return "这里已经安装了本试用版，请使用其维护工具，或选择另一个新文件夹。";
    if (reason.indexOf("owner") >= 0 || reason.indexOf("collision") >= 0 ||
            reason === "install-directory-not-empty" || reason.indexOf("existing-installation") >= 0 ||
            reason.indexOf("legacy-inno") >= 0)
        return "这个位置已有安装或其他文件，请选择一个新的空文件夹。原有文件不会被覆盖。";
    if (reason.indexOf("linked") >= 0)
        return "这个位置包含文件夹链接，请选择本机磁盘上的普通文件夹。";
    return "这个位置暂时无法用于安装。请选一个本机磁盘上的新文件夹，并确认有写入权限。";
}

Component.prototype.installLocationLoaded = function() {
    if (installer.value("TrialNativeHandoff", "false") === "true") {
        this.addExistingUpdate(QInstaller.TargetDirectory);
        return;
    }
    if (!installer.addWizardPage(component, "InstallLocation", QInstaller.TargetDirectory))
        throw new Error("无法加载安装位置页面");
    var widget = gui.pageWidgetByObjectName("DynamicInstallLocation");
    widget.windowTitle = "选择安装位置";
    widget.InstallPath.text = installer.toNativeSeparators(installer.value("TargetDir"));
    try {
        var discovery = JSON.parse(installer.value("TrialInstallDiscovery", "{}"));
        if (discovery.mode === "select") {
            var message = discovery.reason === "multiple-valid-installations" ?
                "发现多个有效安装，请明确选择要更新的旧安装根目录，或使用新的空文件夹。" :
                "已有安装登记需要检查，请明确选择安装位置。";
            for (var n = 0; n < discovery.candidates.length; ++n)
                message += "\n" + discovery.candidates[n].target;
            for (var n = 0; n < discovery.rejected.length; ++n)
                message += "\n" + discovery.rejected[n].target + "：" + discovery.rejected[n].reason;
            widget.LocationHelp.text = message;
        }
    } catch (error) {}
    widget.BrowseButton.clicked.connect(this, Component.prototype.browseLocation);
    widget.InstallPath.textChanged.connect(this, Component.prototype.locationChanged);
    installer.setValidatorForCustomPage(component, "InstallLocation", "validateLocation");
    this.locationChanged(widget.InstallPath.text);
};

Component.prototype.addExistingUpdate = function(beforePage) {
    if (installer.value("TrialExistingUpdateAdded", "false") === "true") return;
    if (!installer.addWizardPage(component, "ExistingUpdate", beforePage))
        throw new Error("无法加载更新页面");
    installer.setValue("TrialExistingUpdateAdded", "true");
    installer.setValue("TrialHandoffTarget", installer.value("TargetDir"));
    installer.setDefaultPageVisible(QInstaller.ReadyForInstallation, false);
    installer.setDefaultPageVisible(QInstaller.PerformInstallation, false);
    installer.setDefaultPageVisible(QInstaller.InstallationFinished, false);
    var update = gui.pageWidgetByObjectName("DynamicExistingUpdate");
    update.windowTitle = "更新星图";
    update.ExistingPath.text = installer.toNativeSeparators(installer.value("TargetDir"));
    update.UpdateButton.clicked.connect(this, Component.prototype.updateExisting);
    update.UpdatePollButton.visible = false;
    update.UpdatePollButton.clicked.connect(this, Component.prototype.updateExistingTick);
    update.CloseButton.clicked.connect(function() { gui.rejectWithoutPrompt(); });
    update.complete = false;
    installer.setValidatorForCustomPage(component, "ExistingUpdate", "preventFreshInstall");
};

Component.prototype.locationChanged = function(text) {
    var widget = gui.pageWidgetByObjectName("DynamicInstallLocation");
    installer.setValue("TargetDir", text.trim());
    widget.FinalPath.text = "最终安装位置：\n" + text.trim();
    widget.LocationError.text = "";
    widget.complete = text.trim() !== "";
};

Component.prototype.browseLocation = function() {
    var parent = QFileDialog.getExistingDirectory("选择安装位置（将创建“星图试用版”文件夹）",
        installer.value("HomeDir"), "TrialInstallParent");
    if (!parent) return;
    parent = parent.replace(/\\/g, "/").replace(/\/+$/, "");
    var path = /\/星图试用版$/.test(parent) ? parent : parent + "/星图试用版";
    gui.pageWidgetByObjectName("DynamicInstallLocation").InstallPath.text = installer.toNativeSeparators(path);
};

Component.prototype.validateLocation = function() {
    var upgrade = JSON.parse(installer.value("TrialNativeUpgradeFiles", "[]")).length > 0;
    var result = installer.execute(installer.value("TrialHelper"), [upgrade ? "bootstrap-check" : "check-target", installer.value("TargetDir")]);
    if (upgrade && result.length === 2 && result[1] === 10) {
        installer.setValue("TrialNativeHandoff", "true");
        // Validation has no update side effect. Both automatic and explicit
        // old-root selection continue to the same stateful update page.
        this.addExistingUpdate(QInstaller.ReadyForInstallation);
        return true;
    }
    if (result.length === 2 && result[1] === 0) return true;
    gui.pageWidgetByObjectName("DynamicInstallLocation").LocationError.text = locationProblem(result);
    return false;
};

Component.prototype.preventFreshInstall = function() { return false; };

Component.prototype.updateExisting = function() {
    if (installer.value("TrialHandoffBusy", "false") === "true" ||
            installer.value("TrialHandoffUnknown", "false") === "true" ||
            installer.value("TrialHandoffSuccess", "false") === "true") return false;
    var widget = gui.pageWidgetByObjectName("DynamicExistingUpdate");
    installer.setValue("TrialHandoffBusy", "true");
    installer.setValue("TrialHandoffFailure", "");
    widget.UpdateButton.enabled = false;
    widget.CloseButton.enabled = true;
    installer.setMessageBoxAutomaticAnswer("cancelInstallation", QMessageBox.No);
    widget.UpdateStatus.text = "正在准备离线更新工具。设置、地图包和自建路线将保留。";
    this.updateStage = "prepare";
    this.updateCopyIndex = 0;
    this.updateFiles = JSON.parse(installer.value("TrialNativeUpgradeFiles", "[]"));
    this.updateTarget = installer.value("TrialHandoffTarget");
    this.updateResultFile = installer.value("TrialSupport") + "/update-attempt-" +
        String(Date.now()) + "-" + String(Math.random()).substring(2) + ".json";
    this.updateJob = null;
    // clickButton(objectName, delay) is QtIFW's queued QTimer-backed API.
    // The click handler returns before any large QRC copy or helper work.
    gui.clickButton("UpdatePollButton", 0);
    return false;
};

Component.prototype.finishExistingUpdate = function(success, message, retryable) {
    var widget = gui.pageWidgetByObjectName("DynamicExistingUpdate");
    installer.setValue("TrialHandoffBusy", "false");
    installer.setValue("TrialHandoffUnknown", !success && !retryable ? "true" : "false");
    widget.CloseButton.enabled = true;
    widget.CloseButton.text = "关闭";
    if (success) {
        installer.setValue("TrialHandoffSuccess", "true");
        widget.UpdateStatus.text = "星图已更新。原有设置、地图包和自建路线已保留。\n可关闭安装器后启动星图。";
        widget.UpdateButton.visible = false;
    } else {
        installer.setValue("TrialHandoffFailure", message);
        widget.UpdateStatus.text = (retryable ? "更新未完成：" : "尚未确认更新完成：") + message +
            "\n原有资料与恢复现场已保留。" + (retryable ?
            "请关闭正在运行的星图，检查磁盘空间和目录权限后重试。" :
            "尚不能确认支持工具的状态。请保留现场，关闭向导后重新运行同一个 Setup 检查。");
        widget.UpdateButton.enabled = retryable;
    }
};

Component.prototype.updateExistingTick = function() {
    if (installer.value("TrialHandoffBusy", "false") !== "true") return;
    var widget = gui.pageWidgetByObjectName("DynamicExistingUpdate");
    var support = installer.value("TrialSupport");
    var helper = installer.value("TrialHelper");
    var retryable = true;
    try {
        if (this.updateStage === "prepare") {
            if (installer.value("TrialNativeSupportReady", "false") !== "true") {
                if (this.updateCopyIndex < this.updateFiles.length) {
                    var relative = this.updateFiles[this.updateCopyIndex];
                    var slash = relative.lastIndexOf("/");
                    if (slash >= 0 && !installer.performOperation("Mkdir", [support + "/" + relative.substring(0, slash)]))
                        throw new Error("无法准备离线更新目录。");
                    if (!installer.performOperation("Copy", [":/qtifw-upgrade/" + relative, support + "/" + relative]))
                        throw new Error("无法读取随包更新工具。");
                    ++this.updateCopyIndex;
                    widget.UpdateStatus.text = "正在准备离线更新工具（" + this.updateCopyIndex + "/" + this.updateFiles.length + "）。";
                    gui.clickButton("UpdatePollButton", 0);
                    return;
                }
                var attrib = installer.environmentVariable("SystemRoot") + "/System32/attrib.exe";
                var writable = installer.execute(attrib, ["-R", installer.toNativeSeparators(support + "/boot/*"), "/S"]);
                if (writable.length !== 2 || writable[1] !== 0) throw new Error("无法准备可写的更新工具。");
                installer.setValue("TrialNativeSupportReady", "true");
            }
            this.updateStage = "launch";
            gui.clickButton("UpdatePollButton", 0);
            return;
        }
        if (this.updateStage === "launch") {
            retryable = false; // A malformed starter reply cannot prove no child was started.
            var started = installer.execute(helper, ["bootstrap-start", this.updateTarget,
                "--result-file", this.updateResultFile, "--setup-source", installer.value("InstallerFilePath"), "--interactive"]);
            this.updateJob = nativeUpdateResult(started);
            function normal(path) { return path.replace(/\\/g, "/").toLowerCase(); }
            if (started[1] !== 0 || this.updateJob.started !== true) {
                retryable = this.updateJob.started === false;
                throw new Error(this.updateJob.reason || "无法启动更新支持工具。");
            }
            if (this.updateJob.instance !== installer.value("TrialInstallerInstance") ||
                    normal(this.updateJob.target) !== normal(this.updateTarget) ||
                    normal(this.updateJob.resultFile) !== normal(this.updateResultFile) ||
                    !(this.updateJob.pid > 0) || !this.updateJob.created)
                throw new Error("更新支持工具返回了不匹配的任务。");
            this.updateStage = "wait";
            this.updatePolls = 0;
            widget.CloseButton.text = "关闭向导（更新继续）";
            widget.UpdateStatus.text = "正在更新，请等待维护器完成。关闭此向导不会中断已启动的更新。";
        } else if (this.updateStage === "wait") {
            retryable = false;
            if (installer.fileExists(this.updateResultFile)) {
                var outcome;
                try { outcome = JSON.parse(installer.readFile(this.updateResultFile, "UTF-8")); }
                catch (error) { throw new Error("更新工具未返回完整结果，请保留现场。"); }
                var job = outcome.job || {};
                var expected = this.updateJob;
                function normalResult(path) { return String(path).replace(/\\/g, "/").toLowerCase(); }
                if (job.instance !== expected.instance ||
                        normalResult(job.target) !== normalResult(expected.target) ||
                        normalResult(job.resultFile) !== normalResult(expected.resultFile))
                    throw new Error("更新结果与本次任务不匹配，请保留现场。");
                if (outcome.success === true && normalResult(outcome.target) === normalResult(this.updateTarget)) {
                    this.finishExistingUpdate(true, "", false);
                    return;
                }
                if (outcome.success === false) {
                    retryable = true; // A trusted terminal failure follows release of the helper's leases.
                    throw new Error(outcome.reason || "维护器未完成");
                }
                throw new Error("更新工具未返回可确认的完成状态，请保留现场。");
            }
            if (++this.updatePolls % 5 === 0) {
                var checked = installer.execute(helper, ["bootstrap-status", this.updateTarget,
                    "--process-id", String(this.updateJob.pid), "--process-created", this.updateJob.created]);
                var state = nativeUpdateResult(checked);
                if (checked[1] !== 0 || typeof state.running !== "boolean")
                    throw new Error(state.reason || "无法确认更新支持工具状态。");
                if (!state.running) {
                    // Completion can race the first fileExists check; the
                    // child publishes before exiting, so check once more.
                    if (installer.fileExists(this.updateResultFile)) {
                        gui.clickButton("UpdatePollButton", 0);
                        return;
                    }
                    // The worker may have exited while its native maintenance
                    // child is still alive. No result is not a terminal failure.
                    throw new Error("更新支持工具已退出，但没有返回完成结果，无法确认维护器是否已结束。请保留现场。");
                }
            }
        }
        gui.clickButton("UpdatePollButton", 200);
    } catch (error) {
        this.finishExistingUpdate(false, error.message, retryable);
    }
};

Component.prototype.createOperations = function() {
    if (installer.value("TrialNativeHandoff", "false") === "true")
        throw new Error("已有安装只允许原维护器更新，未启动新装事务。");
    if (installer.value("TrialRootRejected", "") === "true")
        throw new Error("目录覆盖被拒绝。请使用 TrialTarget=已核准测试目录，而不是 --root。");
    function normal(path) { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
    var helper = installer.value("TrialHelper");
    if (installer.isInstaller()) {
        var prepared = installer.value("TrialPreparedTarget", "");
        if (prepared !== "" && normal(prepared) !== normal(installer.value("TargetDir")))
            throw new Error("安装位置已改变，请重新选择安装目录。");
        // Recheck at the actual production entry, even after GUI validation:
        // another process may have created files since the directory page.
        var admitted = installer.execute(helper, ["prepare", installer.value("TargetDir")]);
        if (admitted.length !== 2 || admitted[1] !== 0) throw new Error(locationProblem(admitted));
        installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
        var selection = installer.execute(helper, ["select-data", installer.value("TargetDir"), "keep", "none"]);
        if (selection.length !== 2 || selection[1] !== 0) throw new Error("无法准备资料保留策略，请保留现场并检查日志。");
        installer.setMessageBoxAutomaticAnswer("OverwriteTargetDirectory", QMessageBox.Yes);
        var links = [installer.value("DesktopDir") + "/星图（试用版）.lnk",
            installer.value("UserStartMenuProgramsPath") + "/星图试用版/星图（试用版）.lnk",
            installer.value("UserStartMenuProgramsPath") + "/星图试用版/卸载星图试用版.lnk"];
        for (var i = 0; i < links.length; ++i)
            if (installer.fileExists(links[i]))
                throw new Error("已经存在星图试用版快捷方式。请先用旧试用版的维护工具卸载，再安装此版本。");
    }
    var fault = installer.value("TrialFault", "");
    if (fault === "before-extract") {
        // Extract is a concurrent operation, so an earlier queued Execute is
        // not a pre-extraction checkpoint. Pause during operation preparation.
        var paused = installer.execute(helper, ["pause", installer.value("TargetDir")]);
        if (paused.length !== 2 || paused[1] !== 0) throw new Error("测试中断点未完成");
    }
    component.createOperations();
    // Sources are either the dependency's permanent support or an extracted
    // canonical file. Default Copy UNDO removes aliases before Extract UNDO.
    var copies = __PROGRAM_COPIES__;
    var parents = {};
    for (var i = 0; i < copies.length; ++i) {
        var parent = copies[i][1].substring(0, copies[i][1].lastIndexOf("/"));
        if (!parents[parent]) {
            component.addOperation("Mkdir", parent);
            parents[parent] = true;
        }
        component.addOperation("Copy", copies[i][0], copies[i][1]);
    }
    component.addOperation("CreateShortcut", "@TargetDir@/program/星图.exe",
        "@DesktopDir@/星图（试用版）.lnk", "workingDirectory=@TargetDir@/program");
    component.addOperation("CreateShortcut", "@TargetDir@/program/星图.exe",
        "@UserStartMenuProgramsPath@/星图试用版/星图（试用版）.lnk", "workingDirectory=@TargetDir@/program");
    component.addOperation("CreateShortcut", "@TargetDir@/trial-maintenance.exe",
        "@UserStartMenuProgramsPath@/星图试用版/卸载星图试用版.lnk", "workingDirectory=@TargetDir@");
    // A signal-handler exception cannot fail native installation. Make program
    // integrity an ordinary DO operation before writing the maintenance tool.
    component.addOperation("Execute", "{0}", helper, "verify-program", "@TargetDir@");
    if (installer.value("TrialUpgradeActive", "false") === "true") {
        // QtIFW re-registers the product only on first install. Keep this
        // update in its native operation journal, including normal UNDO.
        var guid = installer.value("ProductUUID", "");
        var hive = installer.value("AllUsers", "false") === "true" ? "HKEY_LOCAL_MACHINE" : "HKEY_CURRENT_USER";
        var registration = hive + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + guid;
        var location = installer.value(registration + "\\InstallLocation", "", QSettings.NativeFormat);
        if (!/^\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}$/i.test(guid) ||
                normal(location) !== normal(installer.value("TargetDir")))
            throw new Error("无法核对原安装的 Windows 版本登记，请保留现场并检查日志。");
        component.addOperation("GlobalConfig", registration, "DisplayVersion", installer.value("ProductVersion"));
    }
    if (fault === "after-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    if (fault === "fail")
        component.addOperation("Execute", "{0}", helper, "fail", "@TargetDir@");
    if (installer.value("TrialPortable") === "true")
        // Last DO means first UNDO, while the full program still exists.
        // Cancel at the confirmation page leaves the original opt-in intact.
        component.addOperation("Execute", "{0}", helper, "noop", "@TargetDir@", "UNDOEXECUTE",
            "@TargetDir@/_installer-support/qtifw-trial-helper.exe", "disable-watcher", "@TargetDir@");
};
