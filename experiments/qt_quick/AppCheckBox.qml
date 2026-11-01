import QtQuick
import QtQuick.Controls.Basic

// Shared check box: rounded square, blue fill with a white tick when checked.
CheckBox {
    id: control
    hoverEnabled: true
    HoverHandler { cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    font.family: "Microsoft YaHei UI"; font.pixelSize: 13
    spacing: 9
    indicator: Rectangle {
        implicitWidth: 20; implicitHeight: 20
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 6
        color: !control.enabled ? "#eef1f6" : control.checked ? (control.hovered ? "#1f50cc" : "#2f66eb") : "white"
        border.width: control.checked ? 0 : control.activeFocus ? 2 : 1.5
        border.color: control.activeFocus ? "#2f66eb" : control.hovered ? "#6f8fd6" : "#a9bad6"
        Behavior on color { ColorAnimation { duration: 110 } }
        Text {
            anchors.centerIn: parent; visible: control.checked
            text: "✓"; color: "white"; font.pixelSize: 14; font.bold: true
        }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text; font: control.font
        color: control.enabled ? "#183a73" : "#8491a7"
        wrapMode: Text.WordWrap; verticalAlignment: Text.AlignVCenter
    }
}
