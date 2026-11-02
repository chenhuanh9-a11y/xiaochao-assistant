import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Non-activating two-stage picker. Keys are routed by the native input seam
// while this window is visible; clicking is an equivalent path.
Window {
    id: picker
    palette: ClientPalette {}
    objectName: "manualMapPickerWindow"
    title: pickerService.title
    visible: false
    width: 620
    height: Math.min(680, 250 + optionsGrid.implicitHeight)
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus

    Rectangle {
        anchors.fill: parent; anchors.margins: 5
        radius: 14; color: "#f7faff"
        border.width: 1; border.color: "#d6e0ef"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                Label {
                    objectName: "pickerTitle"
                    text: pickerService.title
                    color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 19; font.bold: true
                    Layout.fillWidth: true
                }
                AppButton { objectName: "pickerClose"; text: "关闭  Esc"; implicitWidth: 92; onClicked: pickerService.close() }
            }
            Label {
                objectName: "pickerDescription"
                text: pickerService.description
                color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            GridLayout {
                id: optionsGrid
                objectName: "pickerOptions"
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8; rowSpacing: 8
                Repeater {
                    model: pickerService.options
                    delegate: Button {
                        id: optionButton
                        required property var modelData
                        required property int index
                        objectName: "pickerOption" + index
                        Layout.fillWidth: true
                        Layout.preferredHeight: modelData.kind === "map" ? 174 : 58
                        implicitHeight: modelData.kind === "map" ? 174 : 58
                        text: modelData.key + " · " + modelData.label
                              + (modelData.kind === "group" ? "（" + modelData.count + " 张）" : "")
                        Accessible.name: text
                        focusPolicy: Qt.StrongFocus
                        hoverEnabled: true
                        padding: 8
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        contentItem: Column {
                            spacing: 4
                            anchors.centerIn: parent
                            width: parent.width - 16
                            Image {
                                objectName: "pickerThumbnail" + optionButton.index
                                width: visible ? 200 : 0
                                height: visible ? 116 : 0
                                anchors.horizontalCenter: parent.horizontalCenter
                                source: optionButton.modelData.kind === "map"
                                        ? String(optionButton.modelData.thumbnailUrl || "") : ""
                                visible: optionButton.modelData.kind === "map"
                                         && source.toString().length > 0 && status !== Image.Error
                                asynchronous: true
                                cache: true
                                fillMode: Image.PreserveAspectFit
                                sourceSize.width: 200
                                sourceSize.height: 116
                            }
                            Text {
                                width: parent.width
                                text: optionButton.text
                                color: optionButton.enabled ? "#1f50cc" : "#8491a7"
                                font: optionButton.font
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                        }
                        background: Rectangle {
                            radius: 8
                            color: !optionButton.enabled ? "#edf1f6"
                                   : optionButton.down ? "#dce6fc"
                                   : optionButton.hovered ? "#dce6fc" : "#e9effd"
                            border.width: optionButton.activeFocus || optionButton.down ? 2 : 1
                            border.color: !optionButton.enabled ? "#d6e0ef"
                                          : optionButton.activeFocus || optionButton.down ? "#2f66eb"
                                          : optionButton.hovered ? "#8da4c4" : "#cad8ee"
                        }
                        onClicked: pickerService.choose(index)
                    }
                }
            }
            Label {
                objectName: "pickerPage"
                text: pickerService.pageLabel
                color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }
            Label {
                objectName: "pickerHelp"
                text: pickerService.helpText
                color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            Label {
                objectName: "pickerBusy"
                visible: pickerService.busy
                text: pickerService.message
                color: "#b06d00"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
        }
    }
}
