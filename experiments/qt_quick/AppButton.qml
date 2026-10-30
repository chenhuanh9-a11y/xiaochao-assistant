import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property bool selected: false
    implicitHeight: 38
    implicitWidth: Math.max(74, label.implicitWidth + 28 + (iconImage.visible ? 24 : 0))
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 13
    padding: 12
    contentItem: Item {
        implicitWidth: label.implicitWidth + (iconImage.visible ? 24 : 0)
        implicitHeight: Math.max(label.implicitHeight, iconImage.visible ? 18 : 0)
        Row {
            anchors.centerIn: parent; spacing: iconImage.visible ? 6 : 0
            Image { id: iconImage; visible: source.toString().length > 0; source: control.icon.source; width: visible ? 18 : 0; height: 18; anchors.verticalCenter: parent.verticalCenter; sourceSize.width: 36; sourceSize.height: 36; opacity: control.enabled ? 1 : .45 }
            Text {
                id: label
                text: control.text; font: control.font
                color: !control.enabled ? "#8491a7" : control.primary ? "white" : control.selected ? "#1f50cc" : "#354963"
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
        }
    }
    background: Rectangle {
        radius: 8
        color: !control.enabled ? "#edf1f6" : control.primary ? (control.down ? "#1f50cc" : "#2f66eb") : control.selected ? "#dce8ff" : control.hovered ? "#e7effc" : "#f1f5fc"
        border.width: control.activeFocus ? 2 : 0
        border.color: "#2f66eb"
    }
}
