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
        const labels = {"installed": "已安装，可以使用", "not-installed": "未安装", "incomplete": "文件缺失或有改动",
            "unmanaged": "已有文件，暂时无法确认来源", "unverified": "还没确认是否可用", "recovery-required": "上次操作还没完成", "managed-remnants": "有旧图包文件可清理", "bundled": "内置默认资源"}
        return labels[status] || "需要重新核对"
    }
    function stateDetail(row) {
        if (row.status === "bundled") return "安装包自带，已导入的资源和当前选择保持优先。"
        if (row.status === "unmanaged") return "暂时无法确认这些文件是否属于图包，会保留原文件。"
        if (row.status === "incomplete") return "部分文件有改动或无法确认来源，会保留这些文件，删除可能无法全部完成。"
        if (row.status === "managed-remnants") return "包含这个图包的旧备份，或上次操作留下的临时文件。"
        if (row.status === "unverified") return "暂时无法完成核对，请重新检查状态。"
        if (row.status === "recovery-required") return "请点击“恢复记录”，按提示完成上次操作。"
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
            Label { text: "完成上次操作与清理"; color: "#17365e"; font.bold: true }
            Label { text: resourceService.recoveryUsesHelper ? "确认后软件会正常退出，继续处理上次未完成的更改，或清理已完成操作留下的临时文件。完成后请重新打开查看结果。" : resourceService.recoveryExecutionAllowed ? "软件已准备好，可以确认后恢复图包或清理临时文件。" : "请先结束本局并正常退出，重新打开后按提示恢复图包。"; color: "#526785"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            AppButton { text: "处理上次未完成的操作"; visible: resourceService.recoveryRequired; enabled: resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""; onClicked: resourceService.requestRecovery() }
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
                    Label { Layout.fillWidth: true; text: resourceService.packageTitle(modelData.packageId) + " · " + resourceService.recoveryStatusLabel(modelData.status); color: "#526785"; elide: Text.ElideRight }
                    AppButton {
                        objectName: "resourceTransactionAction-" + modelData.transactionId
                        readonly property bool cleanup: modelData.status === "upgrade-cleanup-available" || modelData.status === "upgrade-cleanup-pending"
                        text: cleanup ? "清理临时副本" : "恢复图包"
                        enabled: (cleanup || modelData.pending) && resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""
                        onClicked: resourceService.requestCleanup(modelData.transactionId)
                    }
                }
            }
        }
        // The package list sits on one white card like the other pages.
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 150
            radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
        ListView {
            id: packages
            objectName: "managedPackageList"
            anchors.fill: parent; anchors.margins: 4; anchors.leftMargin: 18; anchors.rightMargin: 12
            clip: true; spacing: 0
            model: panel.packageManager ? panel.packageManager.packageRows.concat(resourceService.packageRows.filter(function(row) { return row.status === "bundled" })) : resourceService.packageRows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                required property var modelData
                required property int index
                width: packages.width; height: Math.max(70, info.implicitHeight + 22)
                RowLayout {
                    anchors.fill: parent; anchors.topMargin: 10; anchors.bottomMargin: 10; spacing: 18
                    ColumnLayout {
                        id: info
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: modelData.title; elide: Text.ElideRight; font.pixelSize: 14; color: "#142b4b"; font.bold: true }
                        Label {
                            Layout.fillWidth: true
                            text: panel.stateLabel(modelData.status) + (modelData.fileCount !== undefined ? " · " + modelData.fileCount + " 个图包文件（含旧副本）" : "")
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
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#edf1f7"; visible: index < packages.count - 1 }
            }
            Label {
                anchors.centerIn: parent; width: Math.min(parent.width, 440); horizontalAlignment: Text.AlignHCenter
                visible: packages.count === 0
                text: panel.operationPending ? "正在核对本地资源…" : panel.recoveryPending ? "上次图包操作还没完成，请正常退出并重启后按提示处理。" : "还没有可管理的图包。可点击“核对状态”，或导入本地 ZIP 图包。"
                wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 13
            }
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
