import QtQuick
import QtQuick.Controls.Basic

Slider {
    id: control
    implicitHeight: 28
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth; height: 4; radius: 2; color: "#cad8ee"
        Rectangle { width: control.visualPosition * parent.width; height: parent.height; radius: 2; color: "#2f66eb" }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 16; height: 16; radius: 8; color: "white"
        border.width: control.activeFocus ? 2 : 1
        border.color: "#2f66eb"
    }
}
