import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs as NativeDialogs

Item {
    id: root
    objectName: "clientHelpPage"
    property string supportMethod: "wechat"
    property bool historyOpen: false
    property bool diagnosticsOptionsOpen: false
    ScrollView {
        id: helpScroll
        objectName: "helpPageScroll"
        anchors.fill: parent; clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: helpScroll.availableWidth; spacing: 12
            Label { text: "排查与帮助"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Rectangle {
                objectName: "helpTutorialCard"
                Layout.fillWidth: true; implicitHeight: tutorialCard.implicitHeight + 36; radius: 12; color: "white"
                ColumnLayout {
                    id: tutorialCard; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        Image { source: "icons/help.png"; sourceSize: Qt.size(48, 48); Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Label { text: "使用教程"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
                        }
                        AppButton { objectName: "helpTutorialStart"; text: "开始学习"; primary: true; onClicked: tutorialService.open("start") }
                    }
                    GridLayout {
                        id: tutorialGrid
                        Layout.fillWidth: true; columns: tutorialGrid.width >= 520 ? 2 : 1; columnSpacing: 10; rowSpacing: 10
                        Repeater {
                            model: tutorialService.chapters
                            delegate: ToolButton {
                                id: tutorialEntry
                                required property var modelData
                                objectName: "helpTutorial-" + modelData.id
                                Layout.fillWidth: true; implicitHeight: 58
                                Accessible.name: modelData.title
                                onClicked: tutorialService.open(modelData.id)
                                contentItem: RowLayout {
                                    spacing: 8
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 3
                                        Label { text: tutorialEntry.modelData.title; font.bold: true; color: "#17365e"; font.pixelSize: 14 }
                                        Label { text: tutorialEntry.modelData.summary; color: "#8a95a8"; font.pixelSize: 13; Layout.fillWidth: true; elide: Text.ElideRight }
                                    }
                                    Label { visible: tutorialEntry.modelData.done; text: "已看完"; color: "#17775a"; font.bold: true; font.pixelSize: 11 }
                                    Label { text: "›"; color: "#8a95a8"; font.pixelSize: 16 }
                                }
                                background: Rectangle { radius: 10; color: tutorialEntry.hovered ? "#e9effd" : "#f5f7fb"; border.width: tutorialEntry.activeFocus ? 2 : 0; border.color: "#2f66eb" }
                            }
                        }
                    }
                }
            }
            Label { text: infoService.version; color: "#526785" }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { objectName: "helpCheckUpdates"; text: infoService.updatePhase === "checking" ? "正在检查…" : infoService.updatePhase === "error" ? "重新检查" : "检查更新"; enabled: !infoService.checking; onClicked: infoService.checkUpdates() }
                AppButton { objectName: "helpExportDiagnostics"; text: "导出诊断 ZIP"; enabled: !infoService.checking; onClicked: infoService.exportDiagnostics() }
                AppButton { objectName: "helpSaveDiagnostics"; text: "另存 ZIP…"; enabled: !infoService.checking; onClicked: saveDiagnostics.open() }
            }
            AppButton { objectName: "helpDiagnosticsOptions"; text: root.diagnosticsOptionsOpen ? "收起反馈设置" : "反馈设置"; onClicked: root.diagnosticsOptionsOpen = !root.diagnosticsOptionsOpen }
            Label { objectName: "helpDiagnosticsPrivacy"; text: "诊断包默认含文字日志和少量局部失败画面。图片可能含昵称或聊天，请检查后再分享；不会自动上传。"; font.pixelSize: 14; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            ColumnLayout {
                visible: root.diagnosticsOptionsOpen; Layout.fillWidth: true; spacing: 10
                AppCheckBox { objectName: "helpAutomaticFailureSampling"; text: "自动保存局部失败画面（本地）"; Layout.fillWidth: true; checked: infoService.automaticFailureSampling; enabled: !infoService.checking; onToggled: infoService.setAutomaticFailureSampling(checked) }
                Label { text: "仅保存已采集的游戏地图与楼层控件区域，按容量和 7 天期限轮换。关闭后停止新增。"; font.pixelSize: 14; textFormat: Text.PlainText; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785" }
                AppCheckBox { objectName: "helpExportAutomaticFailureEvidence"; text: "导出时附上局部失败画面"; Layout.fillWidth: true; checked: infoService.includeAutomaticFailureEvidence; enabled: !infoService.checking; onToggled: infoService.setIncludeAutomaticFailureEvidence(checked) }
                AppCheckBox { objectName: "helpTextOnlyDiagnostics"; text: "仅导出文字，不附图片"; Layout.fillWidth: true; checked: infoService.textOnlyDiagnostics; enabled: !infoService.checking; onToggled: infoService.setTextOnlyDiagnostics(checked) }
                AppCheckBox { objectName: "helpFailureSampling"; text: "额外保存并导出完整失败画面"; Layout.fillWidth: true; checked: infoService.includeFailureEvidence; enabled: !infoService.checking; onToggled: infoService.setIncludeFailureEvidence(checked) }
                Label { text: "完整画面可能包含整个游戏窗口。需开启后复现，重启后关闭。"; font.pixelSize: 14; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                AppButton { objectName: "resetDiagnosticInstallId"; text: "重置随机诊断标识"; enabled: !infoService.checking; onClicked: infoService.resetDiagnosticInstallId() }
            }
            ProgressBar { Layout.fillWidth: true; visible: infoService.checking; indeterminate: true; Accessible.name: infoService.operation }
            Label { visible: !!infoService.operation || infoService.hasError; text: infoService.operation || infoService.message; color: infoService.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
            Label { visible: !!infoService.diagnosticsReceipt.path; text: "诊断文件：" + (infoService.diagnosticsReceipt.path || ""); textFormat: Text.PlainText; color: "#17775a"; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
            ClientProblemsPanel { Layout.fillWidth: true }
            Rectangle {
                visible: !!infoService.release.version
                Layout.fillWidth: true; implicitHeight: notes.implicitHeight+36; color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    id: notes; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 12
                    Label { text: "更新公告 · "+(infoService.release.version || "") + (infoService.hasUnread ? " · 未读" : ""); font.bold: true; color: "#183a73"; textFormat: Text.PlainText }
                    Label { text: (infoService.release.published_date || "") + (infoService.announcementSkipped ? " · 已忽略提醒" : ""); color: "#526785"; textFormat: Text.PlainText }
                    Label { text: infoService.release.notes || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#354f75"; textFormat: Text.PlainText }
                    RowLayout {
                        AppButton { enabled: !infoService.checking && infoService.hasUnread; text: infoService.hasUnread ? "标记已读" : "已读"; onClicked: infoService.markRead() }
                        AppButton { objectName: "helpSkipAnnouncement"; enabled: !infoService.checking && !infoService.announcementSkipped; text: "忽略本次提醒"; onClicked: infoService.skipVersion() }
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
                        Label { text: "v" + modelData.version + " · " + modelData.published_date; color: "#183a73"; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                        Label { objectName: "helpHistoryNotes"; text: modelData.notes; textFormat: Text.PlainText; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: currentFacts.implicitHeight + 40
                radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    id: currentFacts
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 20; spacing: 12
                    Label { text: "当前运行信息"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
                    GridLayout {
                        columns: 2; columnSpacing: 18; rowSpacing: 10; Layout.fillWidth: true
                        Label { text: "版本"; color: "#526785" }
                        Label { text: "星图 " + infoService.version; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Label { text: "显示环境"; color: "#526785" }
                        Label { text: infoService.facts.displayEnvironment; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Label { text: "当前状态"; color: "#526785" }
                        Label { text: infoService.facts.runtimeStatus; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: supportFooter.implicitHeight + 36; radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    id: supportFooter; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                    Label { text: "支持开发"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
                    Label { text: "星图免费使用，赞赏完全自愿。"; color: "#526785" }
                    AppButton { text: "打开赞赏窗口"; onClicked: support.open() }
                }
            }
        }
    }
    NativeDialogs.FileDialog {
        id: saveDiagnostics
        title: "保存诊断 ZIP"; fileMode: NativeDialogs.FileDialog.SaveFile
        nameFilters: ["ZIP 诊断包 (*.zip)"]; defaultSuffix: "zip"
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
