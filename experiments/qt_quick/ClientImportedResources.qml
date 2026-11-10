import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: panel
    property bool active: false
    property bool refreshAllowed: true
    property bool refreshPending: false
    signal importSelectionCancelled()
    readonly property bool operationPending: archiveDialog.visible || resourceService.busy
    readonly property bool recoveryPending: resourceService.recoveryRequired
    readonly property var packageManager: null
    onActiveChanged: if (active) refreshPending = true
    function openImport(season) {
        if (!operationPending) archiveDialog.open()
    }
    Timer {
        interval: 120
        running: panel.active && panel.refreshPending && !panel.operationPending
        onTriggered: {
            panel.refreshPending = false
            resourceService.listPackages()
        }
    }
    function statusText(row) {
        if (row.status === "selected") return "已选用 · " + row.selectedContexts.join("、")
        if (row.status === "saved") return "文件已校验 · 尚未启用"
        if (row.status === "damaged") return "文件或版本记录异常，请重新获取资源包"
        return "已保存 · 内容待校验，尚未启用"
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        Label { text: "本地资源"; color: "#142b4b"; font.pixelSize: 18; font.bold: true }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#354f75"; font.pixelSize: 14
            text: "基础结构、楼层控件和作者显示分别管理。保存新版本不会改变当前启用的资源。"
        }
        RowLayout {
            spacing: 8; Layout.fillWidth: true
            AppButton { objectName: "importPackagesButton"; text: "导入资源包"; primary: true; enabled: !panel.operationPending; onClicked: panel.openImport("") }
            AppButton { objectName: "checkPackagesButton"; text: "检查文件完整性"; enabled: !panel.operationPending; onClicked: resourceService.refresh() }
            AppButton { objectName: "downloadPackagesButton"; text: "下载资源包"; onClicked: clientCoordinator.navigate("downloads") }
            Item { Layout.fillWidth: true }
        }
        ColumnLayout {
            visible: resourceService.recoveryRequired; Layout.fillWidth: true; spacing: 8
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "上次资源切换未完成。请先结束本局，再处理未完成的操作。"; color: "#b8364a" }
            AppButton { objectName: "recoverResourcesButton"; text: "处理未完成的切换"; enabled: panel.refreshAllowed && !panel.operationPending; onClicked: resourceService.requestRecovery() }
        }
        ClientPackageProgress { Layout.fillWidth: true; localService: resourceService }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150
            radius: 16; color: "white"; border.width: 1; border.color: "#d6e0ef"
            ListView {
                id: packages; objectName: "managedPackageList"
                anchors.fill: parent; anchors.margins: 16
                clip: true; boundsBehavior: Flickable.StopAtBounds
                model: resourceService.packageModel
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    required property var modelData
                    required property int index
                    width: packages.width; height: details.implicitHeight + 24
                    ColumnLayout {
                        id: details
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.topMargin: 12
                        spacing: 5
                        Label { Layout.fillWidth: true; text: modelData.title; elide: Text.ElideRight; font.pixelSize: 15; font.bold: true; color: "#142b4b"; textFormat: Text.PlainText }
                        Label { Layout.fillWidth: true; text: "版本 " + modelData.resourceVersion; elide: Text.ElideRight; color: "#354f75"; textFormat: Text.PlainText }
                        Label { objectName: "packageStatusLabel-" + modelData.key; Layout.fillWidth: true; text: panel.statusText(modelData); wrapMode: Text.Wrap; color: modelData.status === "damaged" ? "#b8364a" : modelData.status === "selected" ? "#17775a" : "#526785" }
                        AppButton {
                            objectName: "activatePackageButton-" + modelData.key
                            visible: modelData.canActivate === true
                            enabled: panel.refreshAllowed && !panel.operationPending && !panel.recoveryPending
                            text: "启用 S0 噩梦"
                            onClicked: resourceService.activatePackage(modelData.key, "s0", "nightmare")
                        }
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#edf1f7"; visible: index < packages.count - 1 }
                }
                Label {
                    anchors.centerIn: parent; width: Math.min(parent.width, 440)
                    visible: packages.count === 0; horizontalAlignment: Text.AlignHCenter
                    text: resourceService.busy ? "正在读取本地资源…" : "尚未导入资源。请导入新版资源 ZIP，补齐需要的基础结构、控件或显示资源。"
                    color: "#526785"; wrapMode: Text.Wrap; font.pixelSize: 14
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: resourceService.message; wrapMode: Text.Wrap; color: "#354f75"; font.pixelSize: 14; textFormat: Text.PlainText }
            AppButton { text: "取消操作"; visible: resourceService.canCancel; onClicked: resourceService.cancel() }
        }
    }
    FileDialog {
        id: archiveDialog; objectName: "resourceArchiveDialog"
        title: "选择新版星图资源包"; nameFilters: ["资源包 (*.zip)"]
        onAccepted: resourceService.importArchive(selectedFile.toString())
        onRejected: panel.importSelectionCancelled()
    }
    Component.onCompleted: refreshPending = active
}
