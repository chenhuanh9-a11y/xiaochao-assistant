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
    if (installer.isInstaller() && installer.value("TrialPreparedTarget") !== installer.value("TargetDir"))
        throw new Error("测试目标已改变，请使用 --root 重新启动安装器。");
    var helper = installer.value("TrialHelper");
    var fault = installer.value("TrialFault", "");
    if (fault === "before-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    component.createOperations();
    if (fault === "after-extract")
        component.addOperation("Execute", "{0}", helper, "pause", "@TargetDir@");
    if (fault === "fail")
        component.addOperation("Execute", "{0}", helper, "fail", "@TargetDir@");
};
