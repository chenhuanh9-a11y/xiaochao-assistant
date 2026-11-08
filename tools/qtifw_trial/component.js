function Component() {
    component.loaded.connect(this, function() {
        if (!installer.isCommandLineInstance() && !installer.isInstaller()) {
            if (!installer.addWizardPage(component, "TrialDataPage", QInstaller.ReadyForInstallation))
                throw new Error("无法显示测试资料选择页");
            var page = gui.pageWidgetByObjectName("DynamicTrialDataPage");
            page.keepData.checked = true;
            installer.setValue("DataChoice", "keep");
            page.clearData.toggled.connect(function(checked) {
                installer.setValue("DataChoice", checked ? "clear" : "keep");
            });
        }
    });
}

Component.prototype.createOperations = function() {
    if (installer.value("TrialRootRejected", "") === "true")
        throw new Error("目录覆盖被拒绝。请使用 TrialTarget=已核准测试目录，而不是 --root。");
    function normal(path) { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
    if (installer.isInstaller() && normal(installer.value("TrialPreparedTarget")) !== normal(installer.value("TargetDir")))
        throw new Error("测试目标已改变，请使用 TrialTarget=测试目录重新启动安装器。");
    var helper = installer.value("TrialHelper");
    var fault = installer.value("TrialFault", "");
    if (fault === "before-extract") {
        // Extract is a concurrent operation, so an earlier queued Execute is
        // not a pre-extraction checkpoint. Pause during operation preparation.
        var paused = installer.execute(helper, ["pause", installer.value("TargetDir")]);
        if (paused.length !== 2 || paused[1] !== 0) throw new Error("测试中断点未完成");
    }
    component.createOperations();
    if (fault === "after-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    if (fault === "fail")
        component.addOperation("Execute", "{0}", helper, "fail", "@TargetDir@");
};
