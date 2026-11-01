import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: panel
    readonly property var state: onlinePackageService.snapshot
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "在线图包"; font.pixelSize: 20; font.bold: true; color: "#17365e" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "onlineCheck"; text: "检查在线列表"; enabled: panel.state.canCheck; onClicked: onlinePackageService.checkPackages() }
            AppButton { text: "取消下载"; enabled: panel.state.canCancel; onClicked: onlinePackageService.cancel() }
        }
        Label { text: panel.state.message; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#567096" }
        ProgressBar {
            visible: panel.state.busy; Layout.fillWidth: true
            indeterminate: !panel.state.progress.totalBytes
            from: 0; to: panel.state.progress.totalBytes || 1; value: panel.state.progress.receivedBytes
        }
        Label { visible: panel.state.busy && panel.state.progress.totalBytes > 0; text: Math.round(panel.state.progress.receivedBytes / 1024) + " / " + Math.round(panel.state.progress.totalBytes / 1024) + " KiB"; color: "#567096"; font.pixelSize: 12 }
        ListView {
            objectName: "onlinePackageList"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8; model: panel.state.packages
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width; height: packageContents.implicitHeight + 28
                radius: 12; color: "white"; border.color: modelData.packageId === panel.state.selectedPackageId ? "#8db4fb" : "#e3ebf6"
                ColumnLayout {
                    id: packageContents; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 8
                    Label { text: modelData.title; font.bold: true; color: "#17365e" }
                    Label { text: modelData.summary; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096" }
                    RowLayout {
                        Label { text: (modelData.bytes / 1048576).toFixed(1) + " MiB"; color: "#567096" }
                        Item { Layout.fillWidth: true }
                        AppButton { text: "选择"; selected: modelData.packageId === panel.state.selectedPackageId; onClicked: onlinePackageService.selectPackage(modelData.packageId) }
                        AppButton { text: "下载并检查导入"; primary: true; enabled: panel.state.canDownload; onClicked: onlinePackageService.downloadPackage(modelData.packageId) }
                    }
                }
            }
        }
        Label { visible: panel.state.stage === "handoff"; text: panel.state.localMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096" }
        Label { text: "下载完成不等于安装完成。后续覆盖确认与取消使用本地资源流程。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"; font.pixelSize: 12 }
    }
}
