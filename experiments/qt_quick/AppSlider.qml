import QtQuick
import QtQuick.Controls.Basic

// Pill slider shared by every page: filled progress, a round thumb that grows
// and shows a soft ring on hover, focus or drag (same look as the Tk PillSlider).
Slider {
    id: control
    implicitHeight: 30
    hoverEnabled: true
    HoverHandler { cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    readonly property bool active: control.enabled && (control.hovered || control.pressed || control.activeFocus)
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth; height: 6; radius: 3; color: "#dfe5f0"
        Rectangle {
            width: control.visualPosition * parent.width; height: parent.height; radius: 3
            color: !control.enabled ? "#b7c1d3" : control.pressed ? "#1f50cc" : "#2f66eb"
        }
    }
    handle: Item {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 24; height: 24
        Rectangle {
            anchors.centerIn: parent; width: 26; height: 26; radius: 13; color: "#c9d6f6"
            opacity: control.active ? .7 : 0
            Behavior on opacity { NumberAnimation { duration: 120 } }
        }
        Rectangle {
            anchors.centerIn: parent
            width: control.active ? 20 : 18; height: width; radius: width / 2; color: "white"
            border.width: 3; border.color: !control.enabled ? "#b7c1d3" : control.pressed ? "#1f50cc" : "#2f66eb"
            Behavior on width { NumberAnimation { duration: 120 } }
        }
    }
}
