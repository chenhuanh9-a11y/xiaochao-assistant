import QtQuick
import QtQuick.Controls.Basic

// Keep manual selection discoverable; occasional actions stay in a local menu.
Flow {
    id: actions
    spacing: 8
    readonly property bool manualActions: session.snapshot.season !== "s1"
        || session.snapshot.manualAlignmentSession === true
    readonly property var floors: !manualActions ? []
        : session.snapshot.season === "s1" && session.snapshot.automaticFloorObservationAvailable === true
        ? ["auto"] : session.snapshot.availableFloors || []
    function available(action) {
        var state = session.snapshot
        return !!state.commands && !!state.commands[action] && state.commands[action].available
            && !session.busy && !state.resourceLocked
    }
    function floorText(floor) {
        if (floor === "auto") return "自动当前楼层"
        if (session.snapshot.season === "s1") return "回退目标 B" + Math.abs(Number(floor))
        return "目标楼层 " + (floor === "B1" ? "B1" : "F" + floor)
    }
    AppButton {
        objectName: "homeSelectMap"; text: "选择地图"
        visible: actions.manualActions
        enabled: actions.available("reselect_map")
        onClicked: clientCoordinator.requestManualMapSelection()
    }
    AppButton {
        id: moreButton
        objectName: "homeMapMore"; text: "更多"
        Accessible.name: "更多本局操作"
        onClicked: moreMenu.open()
        Menu {
            id: moreMenu
            objectName: "homeMapMoreMenu"
            y: moreButton.height
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            MenuItem {
                objectName: "homeRecapture"; text: "回游戏并重新识别"
                enabled: actions.available("capture_now")
                onTriggered: clientCoordinator.recaptureFromMain()
            }
            MenuSeparator { visible: actions.floors.length > 0 }
            Instantiator {
                model: actions.floors
                delegate: MenuItem {
                    required property string modelData
                    objectName: "homeFloor_" + modelData
                    text: actions.floorText(modelData)
                    enabled: actions.available("set_floor")
                    onTriggered: session.dispatchWithPayload("set_floor", {floor: modelData})
                }
                onObjectAdded: function(index, object) { moreMenu.insertItem(index + 2, object) }
                onObjectRemoved: function(index, object) { moreMenu.removeItem(object) }
            }
        }
    }
}
