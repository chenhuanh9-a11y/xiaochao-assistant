import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// 楼层识别方式：自动（默认）、框选（只在玩家框出的范围里读楼层）或固定（旧方式）。
// Choices go into the settings draft; the page's 保存更改 commits them.
ColumnLayout {
    id: root
    objectName: "floorRegionSettings"
    property var service: typeof floorRegionService !== "undefined" ? floorRegionService : null
    readonly property var preferences: settingsService.values
    readonly property bool regionMode: preferences.floorControlMode === "region"
    property bool editing: false
    // Drag in progress, in image item coordinates.
    property point dragStart: Qt.point(0, 0)
    property rect dragRect: Qt.rect(0, 0, 0, 0)
    property bool dragging: false
    spacing: 10

    function openEditor() {
        if (service && service.open()) editing = true
    }
    function closeEditor() {
        if (service) service.close()
        editing = false
        dragging = false
    }
    onVisibleChanged: if (!visible && editing) closeEditor()

    Label { text: "楼层识别"; font.pixelSize: 18; font.bold: true; color: "#142b4b" }
    Label {
        text: {
            if (root.regionMode) return "框选：只在你框出的范围里读楼层；框里读不到时按自动方式识别。"
            if (root.preferences.floorControlMode === "fixed") return "固定：只按游戏默认位置找楼层按钮，和旧版一样，不做自动查找。"
            return "自动：先按默认位置读；读不到时在画面右侧扩大范围查找完整楼层控件。窗口大小、比例变化后仍按当前画面识别。"
        }
        color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true
    }
    RowLayout {
        AppButton { objectName: "floorMode_auto"; text: "自动（推荐）"; selected: root.preferences.floorControlMode !== "fixed" && !root.regionMode; onClicked: settingsService.setValue("floorControlMode", "auto") }
        AppButton { objectName: "floorMode_region"; text: "框选"; selected: root.regionMode; onClicked: settingsService.setValue("floorControlMode", "region") }
        AppButton { objectName: "floorMode_fixed"; text: "固定"; selected: root.preferences.floorControlMode === "fixed"; onClicked: settingsService.setValue("floorControlMode", "fixed") }
        Item { Layout.fillWidth: true }
    }
    ColumnLayout {
        visible: root.regionMode
        Layout.fillWidth: true
        spacing: 8
        Label { objectName: "floorRegionSummary"; text: root.service ? root.service.summary : ""; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        Label {
            text: "请把竖排楼层按钮和完整的数字、负号一起框进去；如果按钮位置会变化，也要把它们能移动到的位置框进去。"
            font.pixelSize: 12; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true
        }
        Flow {
            Layout.fillWidth: true; spacing: 8
            AppButton { objectName: "floorRegionEdit"; text: "框选楼层按钮"; enabled: !!root.service && !root.editing; onClicked: root.openEditor() }
            AppButton { objectName: "floorRegionClear"; text: "清除框选"; enabled: !!root.preferences.floorControlRegion; onClicked: settingsService.setValue("floorControlRegion", null) }
        }
    }
    Label {
        objectName: "floorRegionMessage"
        visible: !!root.service && root.service.message !== ""
        text: root.service ? root.service.message : ""
        color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true
    }
    Rectangle {
        objectName: "floorRegionEditor"
        visible: root.editing && !!root.service && root.service.available
        Layout.fillWidth: true
        Layout.maximumWidth: 760
        implicitHeight: editorContents.implicitHeight + 24
        radius: 12; color: "#f3f7fd"; border.color: "#cad8ee"
        ColumnLayout {
            id: editorContents
            anchors.fill: parent; anchors.margins: 12
            spacing: 8
            Label { text: "在画面上拖出一个框；松开后立即显示框内读到的楼层。"; color: "#354f75"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Image {
                id: frameImage
                objectName: "floorRegionImage"
                Layout.fillWidth: true
                Layout.preferredHeight: sourceSize.width > 0 ? width * sourceSize.height / sourceSize.width : 0
                source: root.service ? root.service.imageUrl : ""
                fillMode: Image.Stretch
                cache: false
                smooth: true
                readonly property var view: root.service ? root.service.viewRect : [0, 0, 1, 1]
                readonly property var draft: root.service ? root.service.draftRect : []
                // Client-normalized <-> image item coordinates.
                function toItem(rect) {
                    return Qt.rect((rect[0] - view[0]) / view[2] * width, (rect[1] - view[1]) / view[3] * height,
                                   rect[2] / view[2] * width, rect[3] / view[3] * height)
                }
                Rectangle {
                    objectName: "floorRegionDraft"
                    readonly property rect shown: root.dragging ? root.dragRect
                        : frameImage.draft.length === 4 ? frameImage.toItem(frameImage.draft) : Qt.rect(0, 0, 0, 0)
                    x: shown.x; y: shown.y; width: shown.width; height: shown.height
                    visible: width > 0 && height > 0
                    color: "#332f66eb"; border.color: "#2f66eb"; border.width: 2
                }
                MouseArea {
                    objectName: "floorRegionDragArea"
                    anchors.fill: parent
                    cursorShape: Qt.CrossCursor
                    function clampPoint(mouse) {
                        return Qt.point(Math.max(0, Math.min(width, mouse.x)), Math.max(0, Math.min(height, mouse.y)))
                    }
                    onPressed: function(mouse) {
                        root.dragStart = clampPoint(mouse)
                        root.dragRect = Qt.rect(root.dragStart.x, root.dragStart.y, 0, 0)
                        root.dragging = true
                    }
                    onPositionChanged: function(mouse) {
                        if (!root.dragging) return
                        var p = clampPoint(mouse)
                        root.dragRect = Qt.rect(Math.min(p.x, root.dragStart.x), Math.min(p.y, root.dragStart.y),
                                                Math.abs(p.x - root.dragStart.x), Math.abs(p.y - root.dragStart.y))
                    }
                    onReleased: function(mouse) {
                        if (!root.dragging) return
                        root.dragging = false
                        var r = root.dragRect, v = frameImage.view
                        if (width <= 0 || height <= 0) return
                        root.service.setDraft(v[0] + r.x / width * v[2], v[1] + r.y / height * v[3],
                                              r.width / width * v[2], r.height / height * v[3])
                    }
                }
            }
            Label { objectName: "floorRegionPreview"; text: root.service ? root.service.preview : ""; color: "#142b4b"; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { objectName: "floorRegionUse"; text: "使用这个范围"; primary: true; onClicked: if (root.service.useDraft()) root.editing = false }
                AppButton { objectName: "floorRegionReload"; text: "换成最新地图画面"; onClicked: root.service.open() }
                AppButton { objectName: "floorRegionCancel"; text: "取消"; onClicked: root.closeEditor() }
            }
        }
    }
}
