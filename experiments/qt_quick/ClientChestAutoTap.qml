import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    property var service: typeof chestAutoTapService !== "undefined" ? chestAutoTapService : null
    property var state: service ? service.snapshot : ({})
    property bool helpExpanded: false
    spacing: 10
    enabled: !!service
    function apply(enabled) {
        if (service) service.update(enabled, hold.currentText, tap.currentText, interval.text)
    }
    Label { text: "开箱连点"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
    Label { text: "按住触发键连点，松开停止。"; font.pixelSize: 14; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    AppSwitch { objectName: "chestAutoTapEnabled"; text: "开启开箱连点"; checked: root.state.savedEnabled || false; onClicked: root.apply(checked) }
    Label { visible: !!root.state.waitingForRound; objectName: "chestRoundPause"; text: "下一局自动恢复"; font.pixelSize: 14; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    RowLayout {
        visible: !!root.state.needsManualResume
        Label { text: "已暂停"; font.pixelSize: 14; color: "#b8364a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        Button { objectName: "chestResume"; text: "重新就绪"; onClicked: root.apply(true) }
    }
    GridLayout {
        columns: width < 420 ? 1 : 3; Layout.fillWidth: true; columnSpacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "按住触发"; color: "#526785" }
            ComboBox { id: hold; objectName: "chestHoldKey"; Layout.fillWidth: true; model: root.state.triggerKeys || []; currentIndex: model.indexOf(root.state.holdKey); onActivated: root.apply(root.state.savedEnabled || false) }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "连发按键"; color: "#526785" }
            ComboBox { id: tap; objectName: "chestTapKey"; Layout.fillWidth: true; model: root.state.tapKeys || []; currentIndex: model.indexOf(root.state.tapKey); onActivated: root.apply(root.state.savedEnabled || false) }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "间隔（毫秒）"; color: "#526785" }
            TextField { id: interval; objectName: "chestInterval"; Layout.fillWidth: true; text: String(root.state.intervalMs || 90); validator: IntValidator { bottom: 20; top: 500 } onEditingFinished: root.apply(root.state.savedEnabled || false) }
        }
    }
    Label { objectName: "chestOperationMessage"; visible: (!root.state.waitingForRound || !!root.state.error) && !root.state.running; text: root.state.message || "请先连接游戏并启用快捷键"; font.pixelSize: 14; color: root.state.error ? "#b8364a" : "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Label { visible: !!root.state.running; text: root.state.status || ""; font.pixelSize: 14; color: "#17775a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    AppButton { objectName: "chestHelpToggle"; text: root.helpExpanded ? "收起说明" : "使用说明"; selected: root.helpExpanded; onClicked: root.helpExpanded = !root.helpExpanded }
    Label {
        objectName: "chestHelpText"; visible: root.helpExpanded
        text: "开启后跨局保留。新一局先松开触发键，再按住。\n切出游戏、输入文字或录入快捷键时暂停。\n绑定变化、按键故障或冲突时，处理提示后点击“重新就绪”。"
        font.pixelSize: 14; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true
    }
}
