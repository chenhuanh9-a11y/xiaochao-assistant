import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property bool selected: false
    implicitHeight: 38
    implicitWidth: Math.max(74, label.implicitWidth + 28)
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 13
    padding: 12
    contentItem: Text {
        id: label
        text: control.text
        font: control.font
        color: !control.enabled ? "#8491a7" : control.primary ? "white" : control.selected ? "#1f50cc" : "#354963"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 8
        color: !control.enabled ? "#edf1f6" : control.primary ? (control.down ? "#1f50cc" : "#2f66eb") : control.selected ? "#dce8ff" : control.hovered ? "#e7effc" : "#f1f5fc"
        border.width: control.activeFocus ? 2 : 0
        border.color: "#2f66eb"
    }
}
