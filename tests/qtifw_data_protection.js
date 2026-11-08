// Executes the production controller with QtIFW UI/process APIs substituted.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('tools/qtifw_trial/controller.js', 'utf8')
    .replaceAll('__SCOPE__', '"/fixture"').replaceAll('__INSTANCE__', '"fixture"')
    .replaceAll('__SUPPORT_FILES__', '[]');
function run({cli = false, values = {}, answers = []} = {}) {
    const calls = [], questions = [];
    values = {TargetDir: '/fixture/targets/install', ...values};
    const page = {};
    const signal = {connect() {}};
    const installer = {
        value: (key, fallback = '') => values[key] === undefined ? fallback : values[key],
        setValue: (key, value) => { values[key] = value; },
        isInstaller: () => false, isUninstaller: () => true,
        isCommandLineInstance: () => cli, setUninstaller() {},
        fileExists: () => true, readFile: () => 'fixture',
        environmentVariable: () => '/fixture-environment-placeholder',
        performOperation: () => true, setDefaultPageVisible() {},
        execute: (_exe, args) => { calls.push(args); return ['{}', 0]; },
        valueChanged: signal, installationFinished: signal,
        toNativeSeparators: x => x, setCanceled() { values.canceled = true; }
    };
    const context = {installer, console: {log() {}},
        QInstaller: {}, QMessageBox: {Yes: 1, No: 2,
            question: (id, ..._args) => { questions.push(id); return answers.shift() ?? 2; }},
        gui: {currentPageWidget: () => page, findChild: () => null, setWizardPageButtonText() {}},
        buttons: {}};
    vm.createContext(context);
    vm.runInContext(source, context);
    vm.runInContext('new Controller()', context);
    if (!cli) vm.runInContext('Controller.prototype.ReadyForInstallationPageCallback()', context);
    return {values, calls, questions};
}
const selected = result => result.calls.filter(args => args[0] === 'select-data').at(-1)[2];
assert.equal(selected(run()), 'keep');
assert.equal(selected(run({values: {DataChoice: 'clear'}})), 'keep'); // GUI never trusts stale clear
assert.equal(selected(run({answers: [1, 1]})), 'clear');
const canceled = run({answers: [1, 2]});
assert.equal(selected(canceled), 'keep');
assert.deepEqual(canceled.questions, ['ChooseUninstallData', 'ConfirmClearData']);
assert.equal(selected(run({cli: true})), 'keep');
assert.throws(() => run({cli: true, values: {DataChoice: 'clear'}}), /ConfirmClear/);
assert.equal(selected(run({cli: true, values: {DataChoice: 'clear', ConfirmClear: 'yes'}})), 'clear');
assert.throws(() => run({cli: true, values: {DataChoice: 'invalid'}}), /请选择/);
// Inspect actual component-created operation lists, including dependency UNDO.
for (const file of ['component.js', 'data-component.js']) {
    const operations = [];
    const template = fs.readFileSync('tools/qtifw_trial/' + file, 'utf8')
        .replaceAll('__SUPPORT_FILES__', '["helper.dll"]')
        .replaceAll('__PROGRAM_COPIES__', '[["source", "@TargetDir@/program/runtime.dll"]]');
    const context = {installer: {value: (key, fallback = '') => ({TrialPortable: 'true',
            TargetDir: '/fixture', TrialPreparedTarget: '/fixture'})[key] ?? fallback,
        isInstaller: () => true, isCommandLineInstance: () => true,
        execute: () => ['{}', 0], fileExists: () => false, setValue() {},
        setMessageBoxAutomaticAnswer() {}}, QMessageBox: {Yes: 1},
        component: {createOperations() {}, addOperation: (...args) => operations.push(args)}};
    vm.createContext(context);
    vm.runInContext(template, context);
    vm.runInContext('Component.prototype.createOperations()', context);
    for (const op of operations.filter(x => x[0] === 'Mkdir'))
        assert.deepEqual(op.slice(-2), ['UNDOOPERATION', '']);
    if (file === 'data-component.js') {
        const undo = operations.find(x => x.includes('undo-data'));
        assert(undo.includes('UNDOEXECUTE'));
    }
}
console.log('QtIFW controller and component policy checks passed');
