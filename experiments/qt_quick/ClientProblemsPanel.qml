import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "recentProblemsPanel"
    property bool expanded: false
    property string openedId: ""
    implicitHeight: contents.implicitHeight + 36
    radius: 12; color: "white"
    ColumnLayout {
        id: contents
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 18; spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "最近问题"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
            Label { text: problemService.recent.length + " 项"; color: "#526785" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "helpProblemsToggle"; text: panel.expanded ? "收起" : "查看详情"; onClicked: panel.expanded = !panel.expanded }
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#526785"
            text: problemService.recent.length ? "保留本次运行最近32项。展开可查看原因并选中复制原始信息。" : "本次运行尚未记录错误或警告。"
        }
        Label { visible: !!problemService.writeError; text: problemService.writeError; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#b8364a" }
        ColumnLayout {
            visible: panel.expanded; Layout.fillWidth: true; spacing: 10
            Repeater {
                model: problemService.recent
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: 8
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#d6e0ef" }
                    ToolButton {
                        objectName: "problemOpen-" + modelData.id
                        Layout.fillWidth: true
                        text: modelData.localTime + " · " + modelData.phaseLabel + " · " + modelData.title + (modelData.count > 1 ? "（" + modelData.count + "次）" : "")
                        onClicked: panel.openedId = panel.openedId === modelData.id ? "" : modelData.id
                        contentItem: Text { text: parent.text; wrapMode: Text.WordWrap; color: "#183a73"; font.bold: true }
                        background: Rectangle { radius: 8; color: parent.hovered ? "#e7effc" : "transparent"; border.width: parent.activeFocus ? 2 : 0; border.color: "#2f66eb" }
                    }
                    Label { text: modelData.summary; textFormat: Text.PlainText; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    ScrollView {
                        visible: panel.openedId === modelData.id
                        Layout.fillWidth: true; Layout.preferredHeight: 240
                        contentWidth: availableWidth; clip: true
                        TextArea {
                            objectName: "problemDetails-" + modelData.id
                            text: problemService.detailText(modelData.id)
                            readOnly: true; selectByMouse: true; persistentSelection: true; activeFocusOnTab: true
                            wrapMode: TextEdit.WrapAnywhere; textFormat: TextEdit.PlainText
                            color: "#354f75"; selectionColor: "#dce8ff"; selectedTextColor: "#142b4b"
                            Accessible.name: "问题详情，可选中复制"
                            background: Rectangle { color: "#f3f6fc"; radius: 6 }
                        }
                    }
                }
            }
        }
    }
}
