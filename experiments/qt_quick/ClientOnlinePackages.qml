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
            Label { text: "在线图包"; font.pixelSize: 20; font.bold: true; color: "#142b4b" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "onlineCheck"; text: "检查在线列表"; enabled: panel.state.canCheck; onClicked: onlinePackageService.checkPackages() }
            AppButton { text: "取消下载"; visible: panel.state.canCancel; enabled: panel.state.canCancel; onClicked: onlinePackageService.cancel() }
        }
        Label { text: panel.state.message; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#567096" }
        ClientPackageProgress {
            Layout.fillWidth: true
            onlineState: panel.state
        }
        ListView {
            objectName: "onlinePackageList"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8; model: panel.state.packages
            ScrollBar.vertical: ScrollBar {}
            Label {
                objectName: "onlinePackageEmpty"
                anchors.centerIn: parent; width: Math.min(parent.width - 24, 420)
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                visible: panel.state.packages.length === 0
                color: "#526785"
                text: panel.state.busy || panel.state.stage === "checking"
                    ? "正在读取在线列表…"
                    : "列表是空的。点右上角「检查在线列表」再获取一次。"
            }
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
                        AppButton {
                            objectName: "onlineDownload-" + modelData.packageId
                            text: modelData.installation && modelData.installation.status === "installed" ? "校验本地安装" : "下载并导入"
                            Accessible.description: "下载完成后自动核对并导入"
                            primary: true; enabled: panel.state.canDownload
                            onClicked: onlinePackageService.downloadPackage(modelData.packageId)
                        }
                    }
                }
            }
        }
        Label { visible: panel.state.stage === "handoff"; text: panel.state.localMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096" }
        Label { text: panel.state.saveOnly ? "基础结构包下载后会自动校验并启用；作者路线需先导入对应基础结构。" : "下载完成后会自动校验并导入，无需手动覆盖。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"; font.pixelSize: 12 }
    }
}
