// THESIS: Existing map workflow, responsive Qt scene graph and persistent views.
// OWN-WORLD: User-pinned white / pale blue, readable blue actions, comfortable sidebar.
// STORY: Browse actual local layers, select, preview, import into an isolated copy.
// FIRST VIEWPORT: 184px sidebar, resource grid and selected-map inspector.
// FORM: Native Operate surface; established visual identity, code-led implementation.
// FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    width: 1180; height: 790
    minimumWidth: 920; minimumHeight: 640
    visible: true
    title: "星图 · Qt 实验版"
    color: "#f3f6fc"
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 13
    property var positions: ({})
    property int createdDelegates: 0
    property int destroyedDelegates: 0
    property int imageErrors: 0
    property int readyImages: 0
    property bool probeAnimating: false
    property real probePhase: 0
    property bool overlayVisible: false
    NumberAnimation on probePhase {
        running: root.probeAnimating
        from: 0; to: 1; duration: 1200
        loops: Animation.Infinite
    }
    Connections {
        target: backend
        function onViewChanging(key) { root.positions[key] = grid.contentY }
        function onViewChanged(key) {
            Qt.callLater(function() {
                grid.contentY = Math.min(root.positions[key] || 0, Math.max(0, grid.contentHeight - grid.height))
            })
        }
        function onConfirmReplacement(message) { confirm.text = message; confirm.open() }
    }
    FileDialog {
        id: filePicker
        title: "导入图包到实验副本"
        nameFilters: ["星图图包 (*.zip)"]
        onAccepted: backend.importArchive(selectedFile.toString())
    }
    Dialog {
        id: confirm
        property string text: ""
        anchors.centerIn: parent
        title: "覆盖实验图包"
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: Label { text: confirm.text; wrapMode: Text.WordWrap; width: 340 }
        onAccepted: backend.confirmImport(true)
        onRejected: backend.confirmImport(false)
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 184
            Layout.fillHeight: true
            color: "#eaf1fd"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 8
                Label { text: "星图"; color: "#183a73"; font.pixelSize: 25; font.bold: true; Layout.topMargin: 12 }
                Label { text: "Qt 实验版"; color: "#526785"; Layout.bottomMargin: 40 }
                AppButton { text: "地图资源"; selected: true; Layout.fillWidth: true }
                AppButton { objectName: "overlayButton"; text: "悬浮预览"; onClicked: backend.showOverlay(); Layout.fillWidth: true }
                Item { Layout.fillHeight: true }
                Label { text: "独立实验副本"; color: "#354f75" }
                Label { text: "未连接游戏识别"; color: "#526785"; font.pixelSize: 12 }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 28
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    spacing: 5
                    Label { text: "地图资源"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
                    Label { text: "浏览本地图层，选择后在右侧预览"; color: "#526785" }
                }
                Item { Layout.fillWidth: true }
                AppButton { objectName: "refreshButton"; text: "刷新"; enabled: !backend.busy; onClicked: backend.refresh() }
                AppButton { text: backend.importing ? "正在导入…" : "导入图包"; primary: true; enabled: !backend.importing; onClicked: filePicker.open() }
            }
            RowLayout {
                spacing: 6
                AppButton { objectName: "nightmareButton"; text: "噩梦"; selected: backend.mode === "nightmare"; onClicked: backend.setMode("nightmare") }
                AppButton { objectName: "difficultButton"; text: "困难"; selected: backend.mode === "difficult"; onClicked: backend.setMode("difficult") }
                Item { Layout.fillWidth: true }
                TextField {
                    id: search
                    objectName: "searchField"
                    Layout.preferredWidth: 230
                    placeholderText: "搜索地图名称或编号"
                    selectByMouse: true
                    color: "#142b4b"
                    placeholderTextColor: "#526785"
                    background: Rectangle { color: "white"; radius: 8; border.color: search.activeFocus ? "#2f66eb" : "#d6e0ef" }
                    onTextEdited: debounce.restart()
                    Timer { id: debounce; interval: 140; onTriggered: backend.search(search.text) }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label { text: "路线"; color: "#526785" }
                ComboBox {
                    id: profile
                    objectName: "profileCombo"
                    Layout.preferredWidth: 168
                    model: backend.profiles
                    textRole: "name"
                    valueRole: "id"
                    implicitHeight: 38
                    leftPadding: 12
                    rightPadding: 26
                    contentItem: Text { text: profile.displayText; color: "#183a73"; font: profile.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                    background: Rectangle { color: "white"; radius: 8; border.color: profile.activeFocus ? "#2f66eb" : "#d6e0ef" }
                    indicator: Canvas {
                        x: profile.width - 21; y: (profile.height - 6) / 2; width: 10; height: 6
                        onPaint: { var c = getContext("2d"); c.beginPath(); c.moveTo(0,0); c.lineTo(10,0); c.lineTo(5,6); c.closePath(); c.fillStyle = "#526785"; c.fill() }
                    }
                    delegate: ItemDelegate { required property var modelData; required property int index; width: profile.width; text: modelData.name; highlighted: profile.highlightedIndex === index }
                    currentIndex: {
                        for (var i = 0; i < backend.profiles.length; i++)
                            if (backend.profiles[i].id === backend.profileId) return i
                        return -1
                    }
                    onActivated: backend.setProfile(currentValue)
                }
                Label { text: "楼层"; color: "#526785"; Layout.leftMargin: 12 }
                Repeater {
                    model: [{name:"全部", value:""}, {name:"1楼", value:"1"}, {name:"2楼", value:"2"}, {name:"B1", value:"B1"}]
                    AppButton { required property var modelData; text: modelData.name; selected: backend.floor === modelData.value; implicitWidth: 48; onClicked: backend.setFloor(modelData.value) }
                }
                Item { Layout.fillWidth: true }
                Label { text: backend.count + " 张图层"; color: "#526785" }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                spacing: 20
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    GridView {
                        id: grid
                        objectName: "resourceGrid"
                        anchors.fill: parent
                        clip: true
                        cellWidth: width / Math.max(2, Math.floor(width / 240))
                        cellHeight: 206
                        model: resourceModel
                        cacheBuffer: 210
                        reuseItems: true
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Item {
                            id: cell
                            required property string key
                            required property string title
                            required property string floor
                            required property string variant
                            required property string imageUrl
                            required property bool available
                            width: grid.cellWidth; height: grid.cellHeight
                            Component.onCompleted: root.createdDelegates++
                            Component.onDestruction: root.destroyedDelegates++
                            Rectangle {
                                anchors.fill: parent; anchors.rightMargin: 12; anchors.bottomMargin: 12
                                radius: 12
                                color: "white"
                                border.width: backend.selectedKey === cell.key || activeFocus ? 2 : 0
                                border.color: "#2f66eb"
                                Rectangle {
                                    x: 8; y: 8; width: parent.width - 16; height: 132
                                    color: "#edf2fa"; radius: 7
                                    Image {
                                        id: thumb
                                        anchors.fill: parent; anchors.margins: 5
                                        source: cell.imageUrl
                                        asynchronous: true
                                        sourceSize: Qt.size(440, 250)
                                        fillMode: Image.PreserveAspectFit
                                        onStatusChanged: {
                                            if (status === Image.Error) root.imageErrors++
                                            if (status === Image.Ready) root.readyImages++
                                        }
                                    }
                                    Label { anchors.centerIn: parent; visible: thumb.status !== Image.Ready; text: !cell.available ? "未安装此图层" : thumb.status === Image.Error ? "图片无法读取" : "载入中…"; color: "#526785" }
                                }
                                Label { x: 14; y: 149; width: parent.width - 28; text: cell.title; elide: Text.ElideRight; font.bold: true; color: "#142b4b" }
                                Label { x: 14; y: 172; width: parent.width - 28; text: (cell.floor === "B1" ? "B1" : cell.floor + "楼") + " · " + cell.variant; elide: Text.ElideRight; font.pixelSize: 11; color: "#526785" }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.select(cell.key) }
                                Accessible.role: Accessible.Button
                                Accessible.name: cell.title + " " + cell.floor + "楼"
                                Accessible.onPressAction: backend.select(cell.key)
                                activeFocusOnTab: true
                                Keys.onReturnPressed: backend.select(cell.key)
                            }
                        }
                    }
                    Label { anchors.centerIn: parent; width: parent.width - 30; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; visible: backend.count === 0; text: backend.busy ? "正在读取本地资源…" : "暂无符合条件的图层\n可切换路线、楼层，或导入图包"; color: "#526785"; lineHeight: 1.5 }
                }
                Rectangle {
                    Layout.preferredWidth: 252; Layout.fillHeight: true
                    radius: 12; color: "white"
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 18
                        spacing: 14
                        Label { text: backend.selectedTitle; font.pixelSize: 17; font.bold: true; color: "#142b4b"; Layout.fillWidth: true; elide: Text.ElideRight }
                        Image { objectName: "selectedPreview"; source: backend.selectedImage; asynchronous: true; sourceSize: Qt.size(620, 620); Layout.fillWidth: true; Layout.fillHeight: true; fillMode: Image.PreserveAspectFit }
                        Label { text: backend.selectedFloor ? "当前图层：" + backend.selectedFloor + "楼" : "点击左侧地图查看"; color: "#526785" }
                        AppButton { text: "在悬浮窗查看"; Layout.fillWidth: true; enabled: backend.selectedImage !== ""; onClicked: backend.showOverlay() }
                        CheckBox {
                            id: through
                            objectName: "clickThroughCheck"; text: "悬浮窗鼠标穿透"; spacing: 8
                            checked: backend.clickThrough
                            indicator: Rectangle {
                                x: through.leftPadding; y: (through.height - height) / 2
                                width: 18; height: 18; radius: 4
                                color: through.checked ? "#2f66eb" : "white"
                                border.color: through.activeFocus ? "#2f66eb" : "#8da4c4"
                                Rectangle { visible: through.checked; anchors.centerIn: parent; width: 8; height: 8; radius: 2; color: "white" }
                            }
                            contentItem: Text { text: through.text; font: through.font; color: "#354f75"; leftPadding: through.indicator.width + through.spacing; verticalAlignment: Text.AlignVCenter }
                            onToggled: backend.setClickThrough(checked)
                        }
                        Label { text: "穿透后可在此关闭"; color: "#526785"; font.pixelSize: 11 }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Rectangle { width: 6; height: 6; radius: 3; color: backend.hasError ? "#b8364a" : backend.busy || backend.importing ? "#2f66eb" : "#17775a" }
                Label { text: backend.message; color: backend.hasError ? "#b8364a" : "#526785"; elide: Text.ElideRight; Layout.fillWidth: true; font.pixelSize: 12 }
            }
        }
    }
    Rectangle { visible: root.probeAnimating; x: 184 + root.probePhase * (root.width - 220); y: root.height - 3; width: 35; height: 3; color: "#2f66eb" }
}
