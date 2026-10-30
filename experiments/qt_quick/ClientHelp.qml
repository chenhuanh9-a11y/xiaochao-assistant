import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs as NativeDialogs

Item {
    id: root
    property string supportMethod: "wechat"
    property bool historyOpen: false
    ScrollView {
        anchors.fill: parent; clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 20
            Label { text: "帮助与公告"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Label { text: infoService.version; color: "#526785" }
            RowLayout {
                AppButton { objectName: "helpCheckUpdates"; text: infoService.updatePhase === "checking" ? "正在检查…" : infoService.updatePhase === "error" ? "重新检查" : "检查更新"; enabled: !infoService.checking; onClicked: infoService.checkUpdates() }
                AppButton { objectName: "helpExportDiagnostics"; text: "导出本次诊断"; enabled: !infoService.checking; onClicked: infoService.exportDiagnostics() }
                AppButton { objectName: "helpSaveDiagnostics"; text: "另存诊断…"; enabled: !infoService.checking; onClicked: saveDiagnostics.open() }
                AppButton { text: "自愿支持开发"; onClicked: support.open() }
            }
            ProgressBar { Layout.fillWidth: true; visible: infoService.checking; indeterminate: true; Accessible.name: infoService.operation }
            Label { text: infoService.operation || infoService.message; color: infoService.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { visible: !!infoService.diagnosticsReceipt.path; text: "诊断文件：" + (infoService.diagnosticsReceipt.path || ""); textFormat: Text.PlainText; color: "#17775a"; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
            Rectangle {
                visible: !!infoService.release.version
                Layout.fillWidth: true; implicitHeight: notes.implicitHeight+36; color: "white"; radius: 12
                ColumnLayout {
                    id: notes; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 12
                    Label { text: "更新公告 · "+(infoService.release.version || "") + (infoService.hasUnread ? " · 未读" : ""); font.bold: true; color: "#183a73" }
                    Label { text: (infoService.release.published_date || "") + (infoService.skippedVersion === infoService.release.version ? " · 已忽略提醒" : ""); color: "#526785" }
                    Label { text: infoService.release.notes || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#354f75"; textFormat: Text.PlainText }
                    RowLayout {
                        AppButton { enabled: !infoService.checking && infoService.hasUnread; text: infoService.hasUnread ? "标记已读" : "已读"; onClicked: infoService.markRead() }
                        AppButton { enabled: !infoService.checking && infoService.skippedVersion !== infoService.release.version; text: "忽略本次提醒"; onClicked: infoService.skipVersion() }
                        AppButton { enabled: !infoService.checking; text: "夸克下载页"; visible: !!(infoService.release.pages && infoService.release.pages.quark); onClicked: infoService.openDownloadPage("quark") }
                        AppButton { enabled: !infoService.checking; text: "UC 下载页"; visible: !!(infoService.release.pages && infoService.release.pages.uc); onClicked: infoService.openDownloadPage("uc") }
                    }
                }
            }
            AppButton { objectName: "helpHistoryToggle"; text: root.historyOpen ? "收起往期更新" : "查看往期更新"; onClicked: root.historyOpen = !root.historyOpen }
            ColumnLayout {
                visible: root.historyOpen; Layout.fillWidth: true; spacing: 12
                Label { visible: !!infoService.historyMessage; text: infoService.historyMessage; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Repeater {
                    model: infoService.history
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 8
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#d6e0ef" }
                        Label { text: "v" + modelData.version + " · " + modelData.published_date; color: "#183a73"; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Label { objectName: "helpHistoryNotes"; text: modelData.notes; textFormat: Text.PlainText; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                }
            }
            Label { text: "先开始，再打开游戏地图"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
            Label { text: "在首页查找并选择游戏窗口，连接后开始本局。需要键盘操作时显式启用快捷键，再切回游戏。结束对局会撤销贴合显示。"; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Label { text: "资源与显示"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
            Label { text: "图包管理会核对实际文件，并在需要覆盖或移除时再次确认。浏览地图选择与运行路线分开。定点图可编辑位置，点击完成才保存，取消恢复原位置。"; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Label { text: "当前程序"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
            Label { text: infoService.facts.entrypoint; color: "#526785"; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; textFormat: Text.PlainText }
            Label { text: infoService.facts.interpreter; color: "#526785"; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; textFormat: Text.PlainText }
        }
    }
    NativeDialogs.FileDialog {
        id: saveDiagnostics
        title: "保存本次诊断"; fileMode: NativeDialogs.FileDialog.SaveFile
        nameFilters: ["JSON 文件 (*.json)"]; defaultSuffix: "json"
        onAccepted: infoService.exportDiagnosticsTo(selectedFile.toString())
    }
    Dialog {
        id: support; parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(410,parent.width-48); height: Math.min(580,parent.height-48)
        title: "支持星图"; modal: true; standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            spacing: 16
            Label { text: "如果星图帮到了你，可以自愿支持开发。"; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                AppButton { text: "微信"; selected: root.supportMethod==="wechat"; onClicked: root.supportMethod="wechat" }
                AppButton { text: "支付宝"; selected: root.supportMethod==="alipay"; onClicked: root.supportMethod="alipay" }
            }
            Image { id: supportCodeImage; cache: false; source: infoService.supportCodes[root.supportMethod]; asynchronous: true; smooth: false; fillMode: Image.PreserveAspectFit; Layout.fillWidth: true; Layout.fillHeight: true; sourceSize: Qt.size(360,360) }
            Label { visible: supportCodeImage.status === Image.Error; text: "支持图片未能读取，请检查资源后重试。"; color: "#b8364a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            AppButton { visible: supportCodeImage.status === Image.Error; text: "重新读取"; onClicked: { supportCodeImage.source = ""; Qt.callLater(function() { supportCodeImage.source = Qt.binding(function() { return infoService.supportCodes[root.supportMethod] }) }) } }
        }
    }
}
