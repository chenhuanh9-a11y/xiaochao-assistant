import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "accountDialog"
    readonly property var state: routePlazaService.snapshot
    property bool showPassword: false
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(420, parent ? parent.width - 32 : 420)
    height: Math.min(
        state.hasRecoveryCode ? 340
            : state.accountMode === "登录" ? 430
            : state.accountMode === "找回密码" ? 560
            : 600,
        parent ? parent.height - 24 : 600
    )
    padding: 0
    topPadding: 0
    bottomPadding: 0
    leftPadding: 0
    rightPadding: 0
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: "#440f172a" }
    background: Rectangle {
        color: "white"
        radius: 16
        border.color: "#d6e0ef"
        border.width: 1
    }

    function clearSecrets() {
        password.clear()
        confirmation.clear()
        code.clear()
        root.showPassword = false
    }

    function submit() {
        routePlazaService.submitAccount(
            identifier.text,
            username.text,
            email.text,
            code.text,
            password.text,
            confirmation.text
        )
        clearSecrets()
    }

    function switchMode(mode) {
        root.clearSecrets()
        routePlazaService.setAccountMode(mode)
        if (mode === "注册") username.forceActiveFocus()
        else identifier.forceActiveFocus()
    }

    onClosed: {
        clearSecrets()
        identifier.clear()
        username.clear()
        email.clear()
        routePlazaService.closeAccount()
    }

    Connections {
        target: routePlazaService
        function onChanged() {
            if (root.state.accountOpen && !root.visible) {
                root.open()
                identifier.forceActiveFocus()
            } else if (!root.state.accountOpen && root.visible) {
                root.close()
            }
        }
    }

    component StyledInput: TextField {
        Layout.fillWidth: true
        implicitHeight: 38
        leftPadding: 12
        rightPadding: 12
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 13
        color: "#142b4b"
        placeholderTextColor: "#94a3b8"
        selectByMouse: true
        background: Rectangle {
            radius: 10
            color: parent.activeFocus ? "white" : "#f8fafd"
            border.width: parent.activeFocus ? 1.5 : 1
            border.color: parent.activeFocus ? "#2f66eb" : "#d6e0ef"
            Behavior on border.color { ColorAnimation { duration: 110 } }
        }
    }

    contentItem: ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Custom Card Header
        Item {
            Layout.fillWidth: true
            implicitHeight: 68

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 22
                anchors.rightMargin: 16
                anchors.topMargin: 18
                anchors.bottomMargin: 8
                spacing: 12

                // Brand Emblem
                Rectangle {
                    width: 38
                    height: 38
                    radius: 11
                    color: "#142b4b"
                    Text {
                        anchors.centerIn: parent
                        text: "✦"
                        color: "#7dd3fc"
                        font.pixelSize: 18
                        font.bold: true
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label {
                        text: root.state.hasRecoveryCode ? "请保存账号恢复码" : "星图账号"
                        color: "#142b4b"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 16
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    Label {
                        text: root.state.hasRecoveryCode
                            ? "确认保存后，本机将不再显示此恢复码"
                            : "与安卓端使用同一个账号，可直接登录"
                        color: "#526785"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 12
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }

                // Close Button (preserves objectName: "accountCancel")
                Button {
                    id: closeBtn
                    objectName: "accountCancel"
                    implicitWidth: 30
                    implicitHeight: 30
                    hoverEnabled: true
                    Accessible.name: "关闭"
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    onClicked: root.close()
                    background: Rectangle {
                        radius: 8
                        color: closeBtn.down ? "#e2e8f0" : closeBtn.hovered ? "#f1f5f9" : "transparent"
                    }
                    contentItem: Text {
                        text: "✕"
                        color: closeBtn.hovered ? "#142b4b" : "#64748b"
                        font.pixelSize: 13
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }

        // Scrollable Body
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: 12

                Item { Layout.fillWidth: true; implicitHeight: 2 }

                // Segmented Pill Switcher (登录 / 注册)
                Rectangle {
                    visible: !root.state.hasRecoveryCode && root.state.accountMode !== "找回密码"
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22
                    implicitHeight: 38
                    radius: 11
                    color: "#eef3fb"

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 4

                        Repeater {
                            model: [
                                { mode: "登录", label: "登录" },
                                { mode: "注册", label: "注册新账号" }
                            ]
                            delegate: Button {
                                id: segBtn
                                required property var modelData
                                readonly property bool isActive: root.state.accountMode === modelData.mode
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                enabled: !root.state.busy
                                hoverEnabled: true
                                HoverHandler { cursorShape: segBtn.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                onClicked: root.switchMode(modelData.mode)
                                background: Rectangle {
                                    radius: 8
                                    color: segBtn.isActive ? "white" : (segBtn.hovered ? "#e3ecfb" : "transparent")
                                    border.width: segBtn.isActive ? 1 : 0
                                    border.color: "#dbe5f5"
                                    Behavior on color { ColorAnimation { duration: 110 } }
                                }
                                contentItem: Text {
                                    text: segBtn.modelData.label
                                    font.family: "Microsoft YaHei UI"
                                    font.pixelSize: 13
                                    font.bold: segBtn.isActive
                                    color: segBtn.isActive ? "#142b4b" : "#526785"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }

                // Recovery Mode Sub-header
                RowLayout {
                    visible: !root.state.hasRecoveryCode && root.state.accountMode === "找回密码"
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22

                    Label {
                        text: "使用恢复码重设密码"
                        color: "#142b4b"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 13
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    Button {
                        id: backToLoginBtn
                        flat: true
                        enabled: !root.state.busy
                        hoverEnabled: true
                        padding: 2
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        onClicked: root.switchMode("登录")
                        background: Item {}
                        contentItem: Text {
                            text: "← 返回登录"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                            color: backToLoginBtn.hovered ? "#1d4ed8" : "#2f66eb"
                        }
                    }
                }

                // Form Fields
                ColumnLayout {
                    visible: !root.state.hasRecoveryCode
                    enabled: !root.state.busy
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22
                    spacing: 10

                    ColumnLayout {
                        visible: root.state.accountMode !== "注册"
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: "用户名或邮箱"
                            color: "#354f75"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        StyledInput {
                            id: identifier
                            objectName: "accountIdentifier"
                            placeholderText: "请输入用户名或邮箱"
                            maximumLength: 320
                            Accessible.name: "用户名或邮箱"
                            onAccepted: root.submit()
                        }
                    }

                    ColumnLayout {
                        visible: root.state.accountMode === "注册"
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: "用户名"
                            color: "#354f75"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        StyledInput {
                            id: username
                            objectName: "accountUsername"
                            placeholderText: "3–32 位英文小写、数字或下划线"
                            maximumLength: 32
                            Accessible.name: "用户名"
                            onAccepted: root.submit()
                        }
                    }

                    ColumnLayout {
                        visible: root.state.accountMode === "注册"
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: "邮箱"
                            color: "#354f75"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        StyledInput {
                            id: email
                            objectName: "accountEmail"
                            placeholderText: "请输入常用邮箱"
                            maximumLength: 320
                            Accessible.name: "邮箱"
                            onAccepted: root.submit()
                        }
                    }

                    ColumnLayout {
                        visible: root.state.accountMode === "找回密码"
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: "恢复码"
                            color: "#354f75"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        StyledInput {
                            id: code
                            objectName: "accountRecoveryInput"
                            placeholderText: "请输入注册时保存的恢复码"
                            maximumLength: 19
                            Accessible.name: "恢复码"
                            onAccepted: root.submit()
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: root.state.accountMode === "找回密码" ? "新密码" : "密码"
                                color: "#354f75"
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 12
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Button {
                                id: forgotBtn
                                visible: root.state.accountMode === "登录"
                                enabled: !root.state.busy
                                flat: true
                                padding: 0
                                hoverEnabled: true
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                onClicked: root.switchMode("找回密码")
                                background: Item {}
                                contentItem: Text {
                                    text: "忘记密码？恢复码找回"
                                    font.family: "Microsoft YaHei UI"
                                    font.pixelSize: 12
                                    color: forgotBtn.hovered ? "#1d4ed8" : "#2f66eb"
                                }
                            }
                        }

                        StyledInput {
                            id: password
                            objectName: "accountPassword"
                            rightPadding: 54
                            placeholderText: root.state.accountMode === "注册" ? "6–128 位密码" : "请输入密码"
                            echoMode: root.showPassword ? TextInput.Normal : TextInput.Password
                            maximumLength: 128
                            Accessible.name: "密码"
                            onAccepted: root.submit()

                            Button {
                                id: togglePwdBtn
                                anchors.right: parent.right
                                anchors.rightMargin: 6
                                anchors.verticalCenter: parent.verticalCenter
                                implicitWidth: 42
                                implicitHeight: 26
                                flat: true
                                hoverEnabled: true
                                focusPolicy: Qt.NoFocus
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                onClicked: root.showPassword = !root.showPassword
                                background: Rectangle {
                                    radius: 6
                                    color: togglePwdBtn.hovered ? "#e8eef8" : "transparent"
                                }
                                contentItem: Text {
                                    text: root.showPassword ? "隐藏" : "显示"
                                    font.family: "Microsoft YaHei UI"
                                    font.pixelSize: 11
                                    color: "#526785"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        visible: root.state.accountMode !== "登录"
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: "确认密码"
                            color: "#354f75"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        StyledInput {
                            id: confirmation
                            objectName: "accountPasswordConfirm"
                            placeholderText: "请再次输入密码"
                            echoMode: root.showPassword ? TextInput.Normal : TextInput.Password
                            maximumLength: 128
                            Accessible.name: "确认密码"
                            onAccepted: root.submit()
                        }
                    }
                }

                // Helper / Security Notice
                Label {
                    objectName: "accountHelpText"
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22
                    visible: !root.state.hasRecoveryCode
                    wrapMode: Text.WordWrap
                    color: "#64748b"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 12
                    text: root.state.accountMode === "注册"
                        ? "注册后会生成专属恢复码，请妥善保存；确认保存后本机不再显示。"
                        : root.state.accountMode === "找回密码"
                        ? "输入注册时保存的恢复码即可重设新密码。"
                        : root.state.persistent
                        ? "本机加密保存登录状态，不保存明文密码。"
                        : "本机不保存登录状态，关闭软件后需要重新登录。"
                }

                // Status / Error Banner
                Rectangle {
                    visible: (root.state.accountMessage || "").length > 0
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22
                    implicitHeight: msgLabel.implicitHeight + 14
                    radius: 8
                    readonly property bool isError: {
                        var m = root.state.accountMessage || ""
                        return m.indexOf("失败") >= 0 || m.indexOf("错误") >= 0 || m.indexOf("无效") >= 0 || m.indexOf("不一致") >= 0 || m.indexOf("为空") >= 0 || m.indexOf("请输入") >= 0
                    }
                    color: isError ? "#fef2f2" : "#f0f6ff"
                    border.color: isError ? "#fecaca" : "#bfdbfe"

                    Label {
                        id: msgLabel
                        objectName: "accountMessage"
                        anchors.fill: parent
                        anchors.margins: 7
                        text: root.state.accountMessage || ""
                        color: parent.isError ? "#b91c1c" : "#1e40af"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }

                // Recovery Code Card (after registration)
                Rectangle {
                    visible: root.state.hasRecoveryCode
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 22
                    implicitHeight: recoveryCol.implicitHeight + 24
                    radius: 12
                    color: "#f0f6ff"
                    border.color: "#bfdbfe"

                    ColumnLayout {
                        id: recoveryCol
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        TextField {
                            objectName: "accountRecoveryCode"
                            Layout.fillWidth: true
                            text: routePlazaService.recoveryCode
                            readOnly: true
                            selectByMouse: true
                            horizontalAlignment: Text.AlignHCenter
                            font.family: "Consolas"
                            font.pixelSize: 19
                            font.bold: true
                            color: "#142b4b"
                            Accessible.name: "账号恢复码"
                            background: Rectangle {
                                radius: 8
                                color: "white"
                                border.color: "#d6e0ef"
                            }
                        }

                        AppButton {
                            Layout.fillWidth: true
                            text: "复制恢复码"
                            onClicked: routePlazaService.copyRecoveryCode()
                        }
                    }
                }

                Item { Layout.fillWidth: true; implicitHeight: 4 }
            }
        }

        // Clean Primary Action Footer
        Item {
            Layout.fillWidth: true
            implicitHeight: 68

            AppButton {
                objectName: "accountSubmit"
                anchors.fill: parent
                anchors.leftMargin: 22
                anchors.rightMargin: 22
                anchors.topMargin: 10
                anchors.bottomMargin: 18
                primary: true
                enabled: !root.state.busy
                text: root.state.hasRecoveryCode
                    ? "我已保存"
                    : root.state.busy
                    ? "请稍候…"
                    : root.state.accountMode === "注册"
                    ? "注册并登录"
                    : root.state.accountMode === "找回密码"
                    ? "重设密码"
                    : "登录"
                onClicked: root.state.hasRecoveryCode ? routePlazaService.finishSignIn() : root.submit()
            }
        }
    }
}
