import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    property var service: typeof chestAutoTapService !== "undefined" ? chestAutoTapService : null
    property var state: service ? service.snapshot : ({})
    spacing: 10
    enabled: !!service
    function apply(enabled) {
        if (service) service.update(enabled, hold.currentText, tap.currentText, interval.text)
    }
    Label { text: "开箱连点"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
    Label { text: "在游戏里按住触发键，连发开箱按键；松开立即停止。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    AppSwitch { objectName: "chestAutoTapEnabled"; text: "开启开箱连点"; checked: root.state.savedEnabled || false; onClicked: root.apply(checked) }
    Label { visible: !!root.state.waitingForRound; objectName: "chestRoundPause"; text: "已开启 · 等待下一局，届时自动恢复"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    RowLayout {
        visible: !!root.state.needsManualResume
        Label { text: "已开启 · 安全暂停，请先检查下方原因"; color: "#b8364a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
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
    Label { text: root.state.message || "请连接游戏并启用快捷键，再手动开启"; color: root.state.error ? "#b8364a" : "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Label { visible: !!root.state.running; text: root.state.status || ""; color: "#17775a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Label { text: "开启选择会跨局保留。结束本局暂停，下一局开始后自动恢复；恢复时请先松开触发键再按住。仅在已连接游戏位于前台时发送；输入文字、录制快捷键或离开游戏时暂停。游戏绑定变化、按键故障或快捷键冲突需检查后重新就绪。"; font.pixelSize: 12; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
}
