import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs as NativeDialogs

Item {
    id: root
    objectName: "clientHelpPage"
    property string supportMethod: "wechat"
    property bool historyOpen: false
    property string openedTopic: ""
    ScrollView {
        id: helpScroll
        objectName: "helpPageScroll"
        anchors.fill: parent; clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: helpScroll.availableWidth; spacing: 12
            Label { text: "排查与帮助"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Label { text: "先看当前状态，再按需要展开步骤或导出排查信息。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { text: infoService.version; color: "#526785" }
            RowLayout {
                AppButton { objectName: "helpCheckUpdates"; text: infoService.updatePhase === "checking" ? "正在检查…" : infoService.updatePhase === "error" ? "重新检查" : "检查更新"; enabled: !infoService.checking; onClicked: infoService.checkUpdates() }
                AppButton { objectName: "helpExportDiagnostics"; text: "导出诊断 ZIP"; enabled: !infoService.checking; onClicked: infoService.exportDiagnostics() }
                AppButton { objectName: "helpSaveDiagnostics"; text: "另存 ZIP…"; enabled: !infoService.checking; onClicked: saveDiagnostics.open() }
                AppButton { text: "自愿支持开发"; onClicked: support.open() }
            }
            CheckBox { text: "附带已保存的失败画面（不截取新画面）"; checked: infoService.includeFailureEvidence; enabled: !infoService.checking; onToggled: infoService.setIncludeFailureEvidence(checked) }
            Label { text: "默认只导出限量脱敏文本、版本和当前状态，不包含原图或个人设置。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            ProgressBar { Layout.fillWidth: true; visible: infoService.checking; indeterminate: true; Accessible.name: infoService.operation }
            Label { text: infoService.operation || infoService.message; color: infoService.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { visible: !!infoService.diagnosticsReceipt.path; text: "诊断文件：" + (infoService.diagnosticsReceipt.path || ""); textFormat: Text.PlainText; color: "#17775a"; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
            ClientProblemsPanel { Layout.fillWidth: true }
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
            Rectangle {
                Layout.fillWidth: true; implicitHeight: currentFacts.implicitHeight + 40
                radius: 12; color: "white"
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
            Repeater {
                model: infoService.helpTopics
                delegate: Rectangle {
                    id: topicCard
                    required property var modelData
                    readonly property bool opened: root.openedTopic === modelData.id
                    property bool showOperationImage: false
                    Layout.fillWidth: true; implicitHeight: topicContents.implicitHeight + 36
                    radius: 12; color: "white"
                    ColumnLayout {
                        id: topicContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 18; spacing: 14
                        ToolButton {
                            objectName: "helpTopic-" + topicCard.modelData.id
                            Layout.fillWidth: true; implicitHeight: 64
                            Accessible.name: topicCard.modelData.title
                            onClicked: root.openedTopic = topicCard.opened ? "" : topicCard.modelData.id
                            contentItem: RowLayout {
                                spacing: 14
                                Image { source: "icons/" + (topicCard.modelData.icon === "keyboard" ? "settings" : topicCard.modelData.icon === "play" ? "panel" : topicCard.modelData.icon) + ".png"; sourceSize: Qt.size(48,48); Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: topicCard.modelData.title; font.pixelSize: 17; font.bold: true; color: "#17365e" }
                                    Label { text: topicCard.modelData.summary; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12; color: "#526785" }
                                }
                                Label { text: topicCard.opened ? "收起" : "展开"; color: "#1f50cc"; font.pixelSize: 12 }
                            }
                            background: Rectangle { radius: 8; color: parent.down ? "#dce6fc" : parent.hovered ? "#e9effd" : "transparent"; border.width: parent.activeFocus ? 2 : 0; border.color: "#2f66eb" }
                        }
                        ColumnLayout {
                            visible: topicCard.opened; Layout.fillWidth: true; spacing: 14
                            Rectangle { Layout.fillWidth: true; height: 1; color: "#d6e0ef" }
                            Repeater {
                                model: topicCard.modelData.steps
                                delegate: RowLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true; spacing: 12; Layout.alignment: Qt.AlignTop
                                    Rectangle {
                                        Layout.alignment: Qt.AlignTop; implicitWidth: 26; implicitHeight: 26; radius: 13; color: "#e9effd"
                                        Label { anchors.centerIn: parent; text: index + 1; color: "#1f50cc" }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 7
                                        Label { text: modelData.text; textFormat: Text.PlainText; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                        Label { visible: !!modelData.bindings; text: modelData.bindings; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                        AppButton { visible: modelData.action === "preferences" || modelData.action === "maps"; text: modelData.action === "preferences" ? "前往设置" : "查看地图资源"; onClicked: infoService.helpNavigate(modelData.action) }
                                    }
                                }
                            }
                            AppButton { objectName: "helpImageToggle-" + topicCard.modelData.id; text: topicCard.showOperationImage ? "收起操作图" : "查看操作图"; onClicked: topicCard.showOperationImage = !topicCard.showOperationImage }
                            Image {
                                id: operationImage
                                objectName: "helpOperationImage-" + topicCard.modelData.id
                                visible: topicCard.showOperationImage
                                Layout.fillWidth: true; Layout.preferredHeight: visible ? width * (sourceSize.height > 0 && sourceSize.width > 0 ? sourceSize.height / sourceSize.width : .75) : 0
                                source: topicCard.opened && visible ? infoService.helpImages[topicCard.modelData.id] : ""
                                asynchronous: true; fillMode: Image.PreserveAspectFit
                                sourceSize.width: 1200
                            }
                            Label { visible: topicCard.showOperationImage; text: "操作图使用演示状态；示例键位以当前设置为准。"; color: "#526785"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Label { visible: topicCard.showOperationImage && operationImage.status === Image.Error; text: "操作图暂不可用，仍可按上方步骤操作。"; color: "#b8364a"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: supportFooter.implicitHeight + 36; radius: 12; color: "white"
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
