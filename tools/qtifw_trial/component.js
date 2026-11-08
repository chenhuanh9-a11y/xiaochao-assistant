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
    if (installer.isInstaller() && installer.value("TrialPreparedTarget") !== installer.value("TargetDir"))
        throw new Error("测试目标已改变，请使用 --root 重新启动安装器。");
    var helper = installer.value("TrialHelper");
    // Runs LAST on uninstall, after Extract UNDO. Default/disarmed on update
    // and failed-install rollback. A cleanup error is a native operation error.
    component.addOperation("Execute", "{0}", helper, "noop", "@TargetDir@",
        "UNDOEXECUTE", helper, "undo-data", "@TargetDir@");
    var fault = installer.value("TrialFault", "");
    if (fault === "before-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    component.createOperations();
    if (fault === "after-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    if (fault === "fail")
        component.addOperation("Execute", "{0}", helper, "fail", "@TargetDir@");
};
