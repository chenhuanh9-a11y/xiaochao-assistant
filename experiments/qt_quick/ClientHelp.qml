import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    property string supportMethod: "wechat"
    ScrollView {
        anchors.fill: parent; clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 20
            Label { text: "帮助与公告"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Label { text: infoService.version; color: "#526785" }
            RowLayout {
                AppButton { text: infoService.checking ? "正在检查…" : "检查更新"; enabled: !infoService.checking; onClicked: infoService.checkUpdates() }
                AppButton { text: "导出本次诊断"; enabled: !infoService.checking; onClicked: infoService.exportDiagnostics() }
                AppButton { text: "自愿支持开发"; onClicked: support.open() }
            }
            Label { text: infoService.message; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Rectangle {
                visible: !!infoService.release.version
                Layout.fillWidth: true; implicitHeight: notes.implicitHeight+36; color: "white"; radius: 12
                ColumnLayout {
                    id: notes; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 12
                    Label { text: "更新公告 · "+(infoService.release.version || ""); font.bold: true; color: "#183a73" }
                    Label { text: infoService.release.notes || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#354f75"; textFormat: Text.PlainText }
                    RowLayout {
                        AppButton { text: "已读"; onClicked: infoService.markRead() }
                        AppButton { text: "忽略本次提醒"; onClicked: infoService.skipVersion() }
                        AppButton { text: "打开下载页"; visible: !!(infoService.release.pages && infoService.release.pages.quark); onClicked: infoService.openDownloadPage("quark") }
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
            Image { source: infoService.supportCodes[root.supportMethod]; asynchronous: true; smooth: false; fillMode: Image.PreserveAspectFit; Layout.fillWidth: true; Layout.fillHeight: true; sourceSize: Qt.size(360,360) }
        }
    }
}
