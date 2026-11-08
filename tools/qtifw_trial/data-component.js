function Component() {}
Component.prototype.createOperations = function() {
    // The real program depends on this component. Dependency ordering removes
    // the program component first, then runs this data policy's UNDO.
    var helper = installer.value("TrialHelper");
    // This dependency can also be explicitly selected on its own. Admit it
    // before any native metadata/Execute operations, just like the program.
    if (installer.isInstaller() && installer.value("TrialPreparedTarget", "") === "") {
        var result = installer.execute(helper, ["prepare", installer.value("TargetDir")]);
        if (result.length !== 2 || result[1] !== 0) throw new Error("安装位置不可用，请选择新的空文件夹。");
        installer.setValue("TrialPreparedTarget", installer.value("TargetDir"));
        result = installer.execute(helper, ["select-data", installer.value("TargetDir"), "keep", "none"]);
        if (result.length !== 2 || result[1] !== 0) throw new Error("无法准备资料保留策略");
    }
    component.addOperation("Execute", "{0}", helper, "noop", "@TargetDir@",
        "UNDOEXECUTE", helper, "undo-data", "@TargetDir@");
};
