function Component() {
    if (installer.isInstaller() && !installer.isCommandLineInstance())
        component.loaded.connect(this, Component.prototype.installLocationLoaded);
}

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
    if (!installer.addWizardPage(component, "InstallLocation", QInstaller.TargetDirectory))
        throw new Error("无法加载安装位置页面");
    var widget = gui.pageWidgetByObjectName("DynamicInstallLocation");
    widget.windowTitle = "选择安装位置";
    widget.InstallPath.text = installer.toNativeSeparators(installer.value("TargetDir"));
    widget.BrowseButton.clicked.connect(this, Component.prototype.browseLocation);
    widget.InstallPath.textChanged.connect(this, Component.prototype.locationChanged);
    installer.setValidatorForCustomPage(component, "InstallLocation", "validateLocation");
    this.locationChanged(widget.InstallPath.text);
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
    var result = installer.execute(installer.value("TrialHelper"), ["check-target", installer.value("TargetDir")]);
    if (result.length === 2 && result[1] === 0) return true;
    gui.pageWidgetByObjectName("DynamicInstallLocation").LocationError.text = locationProblem(result);
    return false;
};

Component.prototype.createOperations = function() {
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
    component.addOperation("CreateShortcut", "@TargetDir@/program/星图.exe",
        "@DesktopDir@/星图（试用版）.lnk", "workingDirectory=@TargetDir@/program");
    component.addOperation("CreateShortcut", "@TargetDir@/program/星图.exe",
        "@UserStartMenuProgramsPath@/星图试用版/星图（试用版）.lnk", "workingDirectory=@TargetDir@/program");
    component.addOperation("CreateShortcut", "@TargetDir@/trial-maintenance.exe",
        "@UserStartMenuProgramsPath@/星图试用版/卸载星图试用版.lnk", "workingDirectory=@TargetDir@");
    // A signal-handler exception cannot fail native installation. Make program
    // integrity an ordinary DO operation before writing the maintenance tool.
    component.addOperation("Execute", "{0}", helper, "verify-program", "@TargetDir@");
    if (installer.value("TrialPortable") === "true")
        component.addOperation("Execute", "{0}", helper, "install-maps", "@TargetDir@");
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
