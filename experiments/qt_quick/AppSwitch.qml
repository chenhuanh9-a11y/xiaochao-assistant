import QtQuick
import QtQuick.Controls.Basic

// Shared on/off switch: the same pill as the settings visibility switch, with
// the label to its right and a short knob slide.
Switch {
    id: control
    hoverEnabled: true
    HoverHandler { cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    font.family: "Microsoft YaHei UI"; font.pixelSize: 13
    spacing: 10
    indicator: Rectangle {
        implicitWidth: 44; implicitHeight: 24
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 12
        color: !control.enabled ? "#e3e8f1" : control.checked ? (control.hovered ? "#1f50cc" : "#2f66eb") : (control.hovered ? "#bccbe5" : "#cad8ee")
        border.width: control.activeFocus ? 2 : 0
        border.color: "#1f50cc"
        Behavior on color { ColorAnimation { duration: 110 } }
        Rectangle {
            width: 18; height: 18; radius: 9; y: 3; color: "white"
            x: 3 + control.visualPosition * (parent.width - width - 6)
            Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text; font: control.font
        color: control.enabled ? "#183a73" : "#8491a7"
        verticalAlignment: Text.AlignVCenter
    }
}
