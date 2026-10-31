import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "accountPage"
    function submit() {
        if (accountService.busy || accountService.loggedIn) return
        accountService.login(identifier.text, password.text)
        password.clear()
    }
    onVisibleChanged: if (!visible) password.clear()
    Connections {
        target: accountService
        function onChanged() { if (accountService.loggedIn) password.clear() }
    }
    ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth
            spacing: 18
            Label { text: "账号"; color: "#183a73"; font.pixelSize: 28; font.bold: true }
            Label { text: "使用已有星途账号登录"; color: "#657b9d"; font.pixelSize: 14 }
            Rectangle {
                Layout.fillWidth: true
                Layout.maximumWidth: 680
                implicitHeight: content.implicitHeight + 48
                color: "white"; radius: 16
                ColumnLayout {
                    id: content
                    anchors.fill: parent; anchors.margins: 24
                    spacing: 14
                    Label {
                        objectName: "accountIdentity"
                        visible: accountService.loggedIn
                        text: "已登录 · " + accountService.username
                        textFormat: Text.PlainText
                        color: "#183a73"; font.pixelSize: 18; font.bold: true
                        Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
                    }
                    ColumnLayout {
                        visible: !accountService.loggedIn
                        Layout.fillWidth: true
                        spacing: 8
                        Label { text: "用户名或邮箱"; color: "#354f75" }
                        TextField {
                            id: identifier
                            objectName: "accountIdentifier"
                            Layout.fillWidth: true
                            enabled: !accountService.busy
                            maximumLength: 320
                            placeholderText: "输入用户名或邮箱"
                            Accessible.name: "用户名或邮箱"
                            inputMethodHints: Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                            onAccepted: password.forceActiveFocus()
                        }
                        Label { text: "密码"; color: "#354f75" }
                        TextField {
                            id: password
                            objectName: "accountPassword"
                            Layout.fillWidth: true
                            enabled: !accountService.busy
                            maximumLength: 128
                            placeholderText: "输入密码"
                            echoMode: TextInput.Password
                            Accessible.name: "密码"
                            inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                            onAccepted: root.submit()
                        }
                    }
                    Label {
                        objectName: "accountStorageWarning"
                        visible: accountService.storageWarning.length > 0
                        text: accountService.storageWarning
                        textFormat: Text.PlainText
                        color: "#b8364a"; font.pixelSize: 13
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Label {
                        objectName: "accountMessage"
                        text: accountService.message
                        textFormat: Text.PlainText
                        color: "#354f75"; font.pixelSize: 13
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        Accessible.role: Accessible.StaticText
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        AppButton {
                            objectName: "accountLogin"
                            text: accountService.busy ? "请稍候…" : "登录"
                            visible: !accountService.loggedIn
                            enabled: !accountService.busy && identifier.text.trim().length > 0 && password.text.length > 0
                            primary: true
                            onClicked: root.submit()
                        }
                        AppButton {
                            objectName: "accountRestore"
                            text: "恢复上次会话"
                            visible: accountService.canRestore
                            enabled: !accountService.busy
                            onClicked: accountService.restoreSession()
                        }
                        AppButton {
                            objectName: "accountCancel"
                            text: "取消"
                            visible: accountService.canCancel
                            onClicked: { password.clear(); accountService.cancel() }
                        }
                        AppButton {
                            objectName: "accountLogout"
                            text: "退出登录"
                            visible: accountService.loggedIn || accountService.canRestore || accountService.storageWarning.length > 0
                            onClicked: { password.clear(); accountService.logout() }
                        }
                    }
                    Label {
                        text: accountService.persistent ? "密码不会保存。登录会话由当前 Windows 用户加密保存。" : "密码不会保存。当前环境关闭后需重新登录。"
                        color: "#657b9d"; font.pixelSize: 12
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
}
