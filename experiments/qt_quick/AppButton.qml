import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property bool selected: false
    // On a blue surface: white chip with blue text (inverse) or a translucent
    // white chip with white text (onColor).
    property bool inverse: false
    property bool onColor: false
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
                color: control.onColor ? (control.enabled ? "white" : "#c9d8ff") : !control.enabled ? "#8491a7" : control.primary ? "white" : "#1f50cc"
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
        }
    }
    // Soft fills instead of hard outlines: secondary buttons read as tinted
    // chips; the outline appears only for keyboard focus and selection.
    background: Rectangle {
        radius: 10
        color: control.onColor ? (control.down ? "#40ffffff" : control.hovered ? "#33ffffff" : "#24ffffff")
            : control.inverse && control.enabled ? (control.down ? "#dfe8ff" : control.hovered ? "#f1f5ff" : "white")
            : !control.enabled ? "#eef1f6" : control.primary ? (control.down ? "#1a46b8" : control.hovered ? "#1f50cc" : "#2f66eb")
            : control.down ? "#d3e0fb" : control.selected ? "#dce8ff" : control.hovered ? "#e0e9fd" : "#eaf0fd"
        border.width: control.activeFocus ? 2 : control.selected ? 1 : 0
        border.color: control.activeFocus ? "#2f66eb" : "#8eaaf0"
        Behavior on color { ColorAnimation { duration: 110 } }
    }
}
