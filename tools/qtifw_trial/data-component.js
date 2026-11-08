function Component() {}
Component.prototype.createOperations = function() {
    // The real program depends on this component. Dependency ordering removes
    // the program component first, then runs this data policy's UNDO.
    var helper = installer.value("TrialHelper");
    component.addOperation("Execute", "{0}", helper, "noop", "@TargetDir@",
        "UNDOEXECUTE", helper, "undo-data", "@TargetDir@");
};
