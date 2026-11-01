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
    font.bold: true
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    HoverHandler { cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    padding: 12
    contentItem: Item {
        implicitWidth: label.implicitWidth + (iconImage.visible ? 24 : 0)
        implicitHeight: Math.max(label.implicitHeight, iconImage.visible ? 18 : 0)
        Row {
            anchors.centerIn: parent; anchors.verticalCenterOffset: control.down ? 1 : 0; spacing: iconImage.visible ? 6 : 0
            Image { id: iconImage; visible: source.toString().length > 0; source: control.icon.source; width: visible ? 18 : 0; height: 18; anchors.verticalCenter: parent.verticalCenter; sourceSize.width: 36; sourceSize.height: 36; opacity: control.enabled ? 1 : .45 }
            Text {
                id: label
                text: control.text; font: control.font
                color: !control.enabled ? "#8491a7" : control.primary ? "white" : "#1f50cc"
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
        }
    }
    background: Rectangle {
        radius: 8
        color: !control.enabled ? "#edf1f6" : control.primary ? (control.down || control.hovered ? "#1f50cc" : "#2f66eb") : control.down ? "#dce6fc" : control.selected ? "#dce8ff" : control.hovered ? "#dce6fc" : "#e9effd"
        border.width: control.activeFocus || control.down ? 2 : 1
        border.color: !control.enabled ? "#d6e0ef" : control.activeFocus || control.down || control.selected ? "#2f66eb" : control.hovered ? "#8da4c4" : "#cad8ee"
    }
}
