import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: panel
    implicitHeight: 330
    property string confirmationText: ""

    function stateLabel(status) {
        const labels = {"installed": "已安装并校验", "not-installed": "未安装", "incomplete": "文件缺失或有改动",
            "unmanaged": "已有文件，尚未登记", "unverified": "尚未完成校验", "recovery-required": "需要恢复事务"}
        return labels[status] || status
    }
    function stateDetail(row) {
        if (row.status === "unmanaged") return "现有文件没有可验证的登记记录，将保留原文件。"
        if (row.status === "incomplete") return "请核对本地改动；移除时会备份实际文件。"
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
            AppButton { text: "核对状态"; enabled: !resourceService.busy && resourceService.pendingKind === ""; onClicked: resourceService.refresh() }
            AppButton { text: "导入 ZIP"; primary: true; enabled: !resourceService.busy && resourceService.pendingKind === "" && !resourceService.recoveryRequired; onClicked: archiveDialog.open() }
        }
        Label {
            Layout.fillWidth: true
            text: "查看作者资源的实际文件状态；导入、更新和备份移除仅作用于此隔离副本。"
            wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 12
        }
        ListView {
            id: packages
            objectName: "managedPackageList"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 150
            clip: true; spacing: 0
            model: resourceService.packageRows
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
                            text: panel.stateLabel(modelData.status) + (modelData.fileCount !== undefined ? " · " + modelData.fileCount + " 个可移除显示文件" : "")
                            color: modelData.status === "installed" ? "#17775a" : "#526785"; font.pixelSize: 12; wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true; visible: text.length > 0
                            text: panel.stateDetail(modelData); color: "#526785"; font.pixelSize: 12; wrapMode: Text.Wrap
                        }
                    }
                    AppButton {
                        text: "备份并移除"; enabled: !!modelData.canRemove && !resourceService.busy && resourceService.pendingKind === "" && !resourceService.recoveryRequired
                        Accessible.name: "备份并移除 " + modelData.title
                        onClicked: resourceService.requestRemove(modelData.packageId)
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#d6e0ef" }
            }
            Label {
                anchors.centerIn: parent; width: Math.min(parent.width, 440); horizontalAlignment: Text.AlignHCenter
                visible: packages.count === 0
                text: resourceService.busy ? "正在核对本地资源…" : resourceService.recoveryRequired ? "资源事务需要恢复，请先核对保留的备份与日志。" : "尚无资源状态。点击“核对状态”，或导入本地 ZIP 资源包。"
                wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 13
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true; text: resourceService.message; wrapMode: Text.Wrap
                color: resourceService.recoveryRequired ? "#b8364a" : "#354f75"; font.pixelSize: 12
                Accessible.role: Accessible.StaticText
            }
            AppButton { text: "取消操作"; visible: resourceService.canCancel; onClicked: resourceService.cancel() }
        }
    }

    FileDialog {
        id: archiveDialog
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
            AppButton { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            AppButton { text: "确认"; primary: true; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            onAccepted: confirmation.accept()
            onRejected: confirmation.reject()
        }
        onAccepted: resourceService.confirmOperation(true)
        onRejected: resourceService.confirmOperation(false)
    }
    Connections {
        target: resourceService
        function onConfirmationRequested(title, message) {
            confirmation.title = title
            panel.confirmationText = message
            confirmation.open()
        }
    }
    Component.onCompleted: resourceService.refresh()
}
