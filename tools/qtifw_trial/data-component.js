function Component() {}
Component.prototype.beginInstallation = function() {
    if (installer.isInstaller() && installer.value("TrialNativeHandoff", "false") === "true")
        throw new Error("已有安装只允许原维护器更新，未启动新装事务。");
};
Component.prototype.createOperations = function() {
    // The real program depends on this component. Dependency ordering removes
    // the program component first, then runs this data policy's UNDO.
    var helper = installer.value("TrialHelper");
    // The uninstaller must work after the installer's temporary files are gone.
    // Its support payload belongs to this dependency, removed after its UNDO.
    var undoHelper = helper;
    if (installer.value("TrialPortable") === "true") {
        component.createOperations();
        undoHelper = "@TargetDir@/_installer-support/qtifw-trial-helper.exe";
    }
    // This dependency can also be explicitly selected on its own. Admit it
    // before any native metadata/Execute operations, just like the program.
    if (installer.isInstaller() && installer.value("TrialPreparedTarget", "") === "") {
        var result = installer.execute(helper, ["prepare", installer.value("TargetDir")]);
        if (result.length !== 2 || result[1] !== 0) throw new Error("安装位置不可用，请选择新的空文件夹。");
        installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
        result = installer.execute(helper, ["select-data", installer.value("TargetDir"), "keep", "none"]);
        if (result.length !== 2 || result[1] !== 0) throw new Error("无法准备资料保留策略");
    }
    if (installer.value("TrialPortable") === "true") {
        // The controller has materialized writable bootstrap files from QRC.
        // Record their permanent copies without adding a second archive.
        var supportFiles = __SUPPORT_FILES__;
        var parents = {};
        for (var i = 0; i < supportFiles.length; ++i) {
            var destination = "@TargetDir@/_installer-support/" + supportFiles[i];
            var parent = destination.substring(0, destination.lastIndexOf("/"));
            if (!parents[parent]) {
                component.addOperation("Mkdir", parent);
                parents[parent] = true;
            }
            component.addOperation("Copy", installer.value("TrialSupport") + "/" + supportFiles[i], destination);
        }
    }
    component.addOperation("Execute", "{0}", helper, "noop", "@TargetDir@",
        "UNDOEXECUTE", undoHelper, "undo-data", "@TargetDir@");
};
