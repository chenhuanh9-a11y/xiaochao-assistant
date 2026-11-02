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
    height: Math.min(560, 250 + optionsGrid.implicitHeight)
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
                    delegate: AppButton {
                        required property var modelData
                        required property int index
                        objectName: "pickerOption" + index
                        Layout.fillWidth: true
                        implicitHeight: 58
                        text: modelData.key + " · " + modelData.label
                              + (modelData.kind === "group" ? "（" + modelData.count + " 张）" : "")
                        Accessible.name: text
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
