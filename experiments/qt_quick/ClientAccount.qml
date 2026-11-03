import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "accountDialog"
    readonly property var state: routePlazaService.snapshot
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(460, parent ? parent.width - 32 : 460)
    height: Math.min(state.hasRecoveryCode ? 340 : state.accountMode === "登录" ? 430 : 640,
                     parent ? parent.height - 24 : 640)
    modal: true; focus: true
    closePolicy: Popup.CloseOnEscape
    title: "星图账号"
    background: Rectangle { color: "white"; radius: 12; border.color: "#d6e0ef" }
    function clearSecrets() { password.clear(); confirmation.clear(); code.clear() }
    function submit() {
        routePlazaService.submitAccount(identifier.text, username.text, email.text, code.text, password.text, confirmation.text)
        clearSecrets()
    }
    onClosed: { clearSecrets(); identifier.clear(); username.clear(); email.clear(); routePlazaService.closeAccount() }
    Connections {
        target: routePlazaService
        function onChanged() {
            if (root.state.accountOpen && !root.visible) { root.open(); identifier.forceActiveFocus() }
            else if (!root.state.accountOpen && root.visible) root.close()
        }
    }
    contentItem: ScrollView {
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 10
            Label { text: "和安卓端使用同一个账号，可直接登录。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                visible: !root.state.hasRecoveryCode
                Layout.fillWidth: true
                Repeater {
                    model: ["登录", "注册", "找回密码"]
                    AppButton {
                        required property string modelData
                        text: modelData; Layout.fillWidth: true; implicitWidth: 0
                        selected: root.state.accountMode === modelData; enabled: !root.state.busy
                        onClicked: {
                            root.clearSecrets(); routePlazaService.setAccountMode(modelData)
                            if (modelData === "注册") username.forceActiveFocus()
                            else identifier.forceActiveFocus()
                        }
                    }
                }
            }
            ColumnLayout {
                visible: !root.state.hasRecoveryCode
                enabled: !root.state.busy
                Layout.fillWidth: true; spacing: 8
                Label { visible: identifier.visible; text: "用户名或邮箱"; color: "#354f75" }
                TextField { id: identifier; objectName: "accountIdentifier"; visible: root.state.accountMode !== "注册"; Layout.fillWidth: true; selectByMouse: true; maximumLength: 320; Accessible.name: "用户名或邮箱"; onAccepted: root.submit() }
                Label { visible: username.visible; text: "用户名"; color: "#354f75" }
                TextField { id: username; objectName: "accountUsername"; visible: root.state.accountMode === "注册"; Layout.fillWidth: true; selectByMouse: true; maximumLength: 32; Accessible.name: "用户名"; onAccepted: root.submit() }
                Label { visible: email.visible; text: "邮箱"; color: "#354f75" }
                TextField { id: email; objectName: "accountEmail"; visible: root.state.accountMode === "注册"; Layout.fillWidth: true; selectByMouse: true; maximumLength: 320; Accessible.name: "邮箱"; onAccepted: root.submit() }
                Label { visible: code.visible; text: "恢复码"; color: "#354f75" }
                TextField { id: code; objectName: "accountRecoveryInput"; visible: root.state.accountMode === "找回密码"; Layout.fillWidth: true; selectByMouse: true; maximumLength: 19; Accessible.name: "恢复码"; onAccepted: root.submit() }
                Label { text: root.state.accountMode === "找回密码" ? "新密码" : "密码"; color: "#354f75" }
                TextField { id: password; objectName: "accountPassword"; Layout.fillWidth: true; echoMode: TextInput.Password; maximumLength: 128; Accessible.name: "密码"; onAccepted: root.submit() }
                Label { visible: confirmation.visible; text: "确认密码"; color: "#354f75" }
                TextField { id: confirmation; objectName: "accountPasswordConfirm"; visible: root.state.accountMode !== "登录"; Layout.fillWidth: true; echoMode: TextInput.Password; maximumLength: 128; Accessible.name: "确认密码"; onAccepted: root.submit() }
            }
            Label {
                objectName: "accountHelpText"
                Layout.fillWidth: true; visible: !root.state.hasRecoveryCode; wrapMode: Text.WordWrap; color: "#526785"; font.pixelSize: 12
                text: root.state.accountMode === "注册" ? "用户名 3–32 位英文小写、数字或下划线；密码 6–128 位。注册后请保存恢复码；确认保存后，本机不再显示。" : root.state.accountMode === "找回密码" ? "用账号恢复码重设密码。" : root.state.persistent ? "本机加密保存登录状态，不保存密码。" : "本机不保存登录状态，关闭软件后需要重新登录。"
            }
            Label { objectName: "accountMessage"; Layout.fillWidth: true; text: root.state.accountMessage; color: "#354f75"; wrapMode: Text.WordWrap }
            TextField {
                objectName: "accountRecoveryCode"; Layout.fillWidth: true; visible: root.state.hasRecoveryCode
                text: routePlazaService.recoveryCode; readOnly: true; selectByMouse: true; font.pixelSize: 19
                Accessible.name: "账号恢复码"
            }
            AppButton { visible: root.state.hasRecoveryCode; text: "复制恢复码"; onClicked: routePlazaService.copyRecoveryCode() }
        }
    }
    footer: Pane {
        padding: 12
        background: Rectangle { color: "#f3f7ff" }
        contentItem: RowLayout {
            spacing: 8
            AppButton {
                objectName: "accountSubmit"; primary: true; enabled: !root.state.busy; Layout.fillWidth: true
                text: root.state.hasRecoveryCode ? "我已保存" : root.state.busy ? "请稍候…" : root.state.accountMode === "注册" ? "注册并登录" : root.state.accountMode === "找回密码" ? "重设密码" : "登录"
                onClicked: root.state.hasRecoveryCode ? routePlazaService.finishSignIn() : root.submit()
            }
            AppButton { objectName: "accountCancel"; text: "取消"; Layout.fillWidth: true; onClicked: root.close() }
        }
    }
}
