import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: root
    objectName: "clientOnboarding"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 32)
    height: Math.min(content.implicitHeight + 48, parent.height - 32)
    padding: 24
    modal: false
    focus: false
    visible: onboardingService.active
    closePolicy: Popup.NoAutoClose
    background: Rectangle { radius: 12; color: "white"; border.color: "#cad8ee" }
    function record(target) {
        if (settingsService.beginShortcutCapture(target)) recorder.forceActiveFocus()
    }
    component Note: Label {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        color: "#526785"; font.pixelSize: 13
    }
    contentItem: ScrollView {
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: content
            width: parent.width; spacing: 16
            Label { text: "准备快捷键"; color: "#142b4b"; font.pixelSize: 22; font.bold: true }
            Note { text: "先核对地图键，再试用游戏控件。完成后即可开始使用。" }
            Label { text: onboardingService.step === 1 ? "1 / 2  游戏地图键" : "2 / 2  试用游戏控件"; color: "#183a73"; font.pixelSize: 16; font.bold: true }
            Note { text: onboardingService.step === 1 ? "和游戏地图按键保持一致；点击修改后按键，再确认保存。" : "用当前快捷键显示一次，再隐藏一次。鼠标按钮也可兜底，来源会单独记录。" }
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: onboardingService.step === 1 ? (settingsService.values.hotkey || "未绑定") : (settingsService.values.actionHotkeys[0] || "未绑定"); font.pixelSize: 20; color: "#183a73" }
                AppButton { text: "修改"; enabled: !settingsService.busy; onClicked: root.record(onboardingService.step === 1 ? "hotkey" : "toggle-panel") }
            }
            ColumnLayout {
                visible: settingsService.shortcutRecording; Layout.fillWidth: true; spacing: 10
                FocusScope {
                    id: recorder
                    objectName: "onboardingRecorder"
                    Layout.fillWidth: true; implicitHeight: 52
                    Keys.priority: Keys.BeforeItem
                    Keys.onPressed: function(event) {
                        if (!event.isAutoRepeat) settingsService.captureShortcutKey(event.key, event.modifiers, event.text, false)
                        event.accepted = true
                    }
                    Keys.onReleased: function(event) {
                        if (!event.isAutoRepeat) settingsService.captureShortcutKey(event.key, event.modifiers, event.text, true)
                        event.accepted = true
                    }
                    Rectangle { anchors.fill: parent; radius: 8; color: "#f1f5fc"; border.color: recorder.activeFocus ? "#2f66eb" : "#cad8ee"; border.width: recorder.activeFocus ? 2 : 1 }
                    Label { anchors.centerIn: parent; text: settingsService.shortcutCandidateReady ? settingsService.shortcutCandidate : "在此按键，或使用右键 / 中键 / 侧键"; color: "#183a73" }
                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton | Qt.BackButton | Qt.ForwardButton
                        onPressed: function(mouse) {
                            if (mouse.button === Qt.LeftButton) recorder.forceActiveFocus()
                            else settingsService.captureShortcutMouse(mouse.button, mouse.modifiers)
                        }
                    }
                }
                Note { text: settingsService.shortcutMessage }
                RowLayout {
                    AppButton { text: "确认到草稿"; enabled: settingsService.shortcutCandidateReady; onClicked: settingsService.confirmShortcutCapture() }
                    AppButton { text: "取消修改"; onClicked: settingsService.cancelShortcutCapture() }
                }
            }
            ColumnLayout {
                visible: onboardingService.step === 2; Layout.fillWidth: true; spacing: 10
                Note { text: onboardingService.trial.panelVisible ? "控件已显示" : "控件已隐藏" }
                Note { text: "显示一次：" + (onboardingService.trial.shown ? "已完成（" + (onboardingService.trial.shownSource === "keyboard" ? "快捷键" : "鼠标兜底") + "）" : "未完成") }
                Note { text: "隐藏一次：" + (onboardingService.trial.hidden ? "已完成（" + (onboardingService.trial.hiddenSource === "keyboard" ? "快捷键" : "鼠标兜底") + "）" : "未完成") }
                RowLayout {
                    AppButton { text: "鼠标兜底显示"; enabled: onboardingService.trial.canShow; onClicked: onboardingService.tryControls(true) }
                    AppButton { text: "鼠标兜底隐藏"; enabled: onboardingService.trial.canHide; onClicked: onboardingService.tryControls(false) }
                }
            }
            Note { visible: text.length > 0; text: onboardingService.message; color: "#b8364a" }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { text: "以后再说"; onClicked: onboardingService.dismiss() }
                AppButton { visible: onboardingService.step === 2; text: "上一步"; enabled: !settingsService.busy; onClicked: onboardingService.back() }
                AppButton { objectName: "onboardingAdvance"; primary: true; text: settingsService.busy ? "正在保存…" : onboardingService.step === 1 ? "下一步：试用控件" : "完成，进入页面"; enabled: !settingsService.busy; onClicked: onboardingService.advance() }
            }
        }
    }
}
