import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: panel
    implicitHeight: 330
    property string confirmationText: ""
    property var packageManager: typeof packageManagerService !== "undefined" ? packageManagerService : null
    property var confirmationOwner: resourceService
    property bool managerRefreshPending: false
    readonly property bool operationPending: resourceService.busy || resourceService.pendingKind !== "" || (packageManager && (packageManager.busy || packageManager.pendingKind !== ""))
    readonly property bool recoveryPending: resourceService.recoveryRequired || (packageManager && packageManager.recoveryRequired)

    function refreshPackages() {
        managerRefreshPending = !!packageManager
        resourceService.refresh()
    }

    function stateLabel(status) {
        const labels = {"installed": "已安装并校验", "not-installed": "未安装", "incomplete": "文件缺失或有改动",
            "unmanaged": "已有文件，尚未登记", "unverified": "尚未完成校验", "recovery-required": "需要恢复事务", "managed-remnants": "受管历史副本"}
        return labels[status] || status
    }
    function stateDetail(row) {
        if (row.status === "unmanaged") return "现有文件没有可验证的登记记录，将保留原文件。"
        if (row.status === "incomplete") return "存在本地改动；不能验证归属的文件与登记会保留，不会报告完整删除。"
        if (row.status === "managed-remnants") return "包含同包旧备份或已结束事务中的受管副本。"
        if (row.status === "unverified") return "暂时无法完成核对，请重新检查状态。"
        if (row.status === "recovery-required") return "请先核对保留的事务备份与日志。"
        return ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "管理本地资源"; color: "#142b4b"; font.pixelSize: 18; font.bold: true }
            Item { Layout.fillWidth: true }
            AppButton { text: "恢复记录"; enabled: !panel.operationPending; onClicked: resourceService.inspectRecovery() }
            AppButton { text: "在线图包"; onClicked: clientCoordinator.navigate("downloads") }
            AppButton { text: "核对状态"; enabled: !panel.operationPending && !panel.recoveryPending; onClicked: panel.refreshPackages() }
            AppButton { objectName: "clearManagedPackagesButton"; text: "清理全部图包"; enabled: !!panel.packageManager && !panel.operationPending && !panel.recoveryPending; onClicked: panel.packageManager.requestClearAll() }
            AppButton { text: "导入 ZIP"; primary: true; enabled: !panel.operationPending && !panel.recoveryPending; onClicked: archiveDialog.open() }
        }
        Label {
            Layout.fillWidth: true
            text: "删除只清理已核对归属的图包和历史副本；成功后不保留包副本。官方基础资源、自定义路线与未知文件会保留。"
            wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 12
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: resourceService.recoveryRequired || resourceService.recoveryTransactions.length > 0
            Label { text: "升级恢复与回滚"; color: "#17365e"; font.bold: true }
            Label { text: resourceService.recoveryUsesHelper ? "确认后正常关闭客户端，由恢复工具还原旧资源；重新打开后核对结果。" : resourceService.recoveryExecutionAllowed ? "已停止资源读取，可核对后恢复旧资源。" : "恢复需要单独的启动恢复阶段，识别和资源读取必须全部停止。"; color: "#526785"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            AppButton { text: "恢复中断的升级"; visible: resourceService.recoveryRequired; enabled: resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""; onClicked: resourceService.requestRecovery() }
            ListView {
                id: transactions
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 156)
                clip: true
                ScrollBar.vertical: ScrollBar {}
                model: resourceService.recoveryTransactions
                delegate: RowLayout {
                    required property var modelData
                    width: transactions.width
                    Label { Layout.fillWidth: true; text: modelData.packageId + " · " + modelData.transactionId.slice(0,8) + " · " + modelData.status; color: "#526785"; elide: Text.ElideRight }
                    AppButton { text: "回滚"; enabled: resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""; onClicked: resourceService.requestRollback(modelData.transactionId) }
                }
            }
        }
        ListView {
            id: packages
            objectName: "managedPackageList"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 150
            clip: true; spacing: 0
            model: panel.packageManager ? panel.packageManager.packageRows : resourceService.packageRows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                required property var modelData
                width: packages.width; height: Math.max(70, info.implicitHeight + 22)
                RowLayout {
                    anchors.fill: parent; anchors.topMargin: 10; anchors.bottomMargin: 10; spacing: 18
                    ColumnLayout {
                        id: info
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: modelData.title; elide: Text.ElideRight; font.pixelSize: 14; color: "#142b4b"; font.bold: true }
                        Label {
                            Layout.fillWidth: true
                            text: panel.stateLabel(modelData.status) + (modelData.fileCount !== undefined ? " · " + modelData.fileCount + " 个受管文件（含历史副本）" : "")
                            color: modelData.status === "installed" ? "#17775a" : "#526785"; font.pixelSize: 12; wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true; visible: text.length > 0
                            text: panel.stateDetail(modelData); color: "#526785"; font.pixelSize: 12; wrapMode: Text.Wrap
                        }
                    }
                    AppButton {
                        objectName: "removePackageButton-" + modelData.packageId
                        text: "删除图包"; enabled: !!panel.packageManager && !!modelData.canRemove && !panel.operationPending && !panel.recoveryPending
                        Accessible.name: "删除图包 " + modelData.title
                        onClicked: panel.packageManager.requestRemove(modelData.packageId)
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#d6e0ef" }
            }
            Label {
                anchors.centerIn: parent; width: Math.min(parent.width, 440); horizontalAlignment: Text.AlignHCenter
                visible: packages.count === 0
                text: panel.operationPending ? "正在核对本地资源…" : panel.recoveryPending ? "资源事务需要恢复，请正常退出并重启后按提示处理。" : "尚无受管图包。可核对状态，或导入本地 ZIP 资源包。"
                wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 13
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true; text: (panel.packageManager ? panel.packageManager.message + "\n" : "") + resourceService.message; wrapMode: Text.Wrap
                color: panel.recoveryPending ? "#b8364a" : "#354f75"; font.pixelSize: 12
                Accessible.role: Accessible.StaticText
            }
            AppButton { text: "取消操作"; visible: resourceService.canCancel; onClicked: resourceService.cancel() }
            AppButton { text: "取消删除"; visible: !!panel.packageManager && panel.packageManager.canCancel; onClicked: panel.packageManager.cancel() }
        }
    }

    FileDialog {
        id: archiveDialog
        objectName: "resourceArchiveDialog"
        title: "选择本地地图资源包"
        nameFilters: ["地图资源包 (*.zip)"]
        onAccepted: resourceService.importArchive(selectedFile.toString())
    }
    Dialog {
        id: confirmation
        objectName: "resourceConfirmationDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 48 : 520)
        modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        contentItem: Label { text: panel.confirmationText; wrapMode: Text.Wrap; color: "#354963"; font.pixelSize: 13; padding: 6 }
        footer: DialogButtonBox {
            AppButton { objectName: "resourceConfirmCancelButton"; text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            AppButton { objectName: "resourceConfirmAcceptButton"; text: "确认"; primary: true; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            onAccepted: confirmation.accept()
            onRejected: confirmation.reject()
        }
        onAccepted: panel.confirmationOwner.confirmOperation(true)
        onRejected: panel.confirmationOwner.confirmOperation(false)
        onClosed: if (panel.confirmationOwner.pendingKind !== "") panel.confirmationOwner.confirmOperation(false)
    }
    Connections {
        target: resourceService
        function onChanged() {
            if (panel.managerRefreshPending && !resourceService.busy && resourceService.pendingKind === "") {
                panel.managerRefreshPending = false
                panel.packageManager.refresh()
            }
        }
        function onConfirmationRequested(title, message) {
            panel.confirmationOwner = resourceService
            confirmation.title = title
            panel.confirmationText = message
            confirmation.open()
        }
    }
    Connections {
        target: panel.packageManager
        function onConfirmationRequested(title, message) {
            panel.confirmationOwner = panel.packageManager
            confirmation.title = title
            panel.confirmationText = message
            confirmation.open()
        }
    }
    Component.onCompleted: panel.refreshPackages()
}
