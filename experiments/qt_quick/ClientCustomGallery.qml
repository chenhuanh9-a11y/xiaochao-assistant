pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: root
    implicitWidth: 1000
    implicitHeight: 680
    readonly property var state: customGalleryService.snapshot
    function selectedIndex(rows, id) {
        for (var i=0; i<rows.length; ++i) if (rows[i].id === id) return i
        return -1
    }
    function askName(operation, initial) {
        nameDialog.operation = operation
        nameField.text = initial
        nameDialog.open()
        nameField.forceActiveFocus()
    }
    Connections {
        target: customGalleryService
        function onChanged() { drawing.requestPaint() }
        function onConfirmationRequested(title, message) {
            confirmation.title=title; confirmationMessage.text=message; confirmation.open()
        }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 14
        RowLayout {
            Label { text: "自定义图集"; color: "#142b4b"; font.pixelSize: 26; font.bold: true }
            Item { Layout.fillWidth: true }
            AppButton { text: "导入"; enabled: !root.state.busy; onClicked: importPicker.open() }
            AppButton { text: "导出"; enabled: !root.state.busy; onClicked: root.askName("export", "gallery.json") }
        }
        Label { text: "使用原有道路规划，路线修改后自动保存。默认图集可复制后编辑。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        RowLayout {
            ComboBox {
                id: galleries
                objectName: "customGalleryChoice"
                Layout.preferredWidth: 220
                model: root.state.galleries; textRole: "name"; valueRole: "id"
                currentIndex: root.selectedIndex(root.state.galleries, root.state.selectedGallery)
                enabled: !root.state.busy && !root.state.pendingKind
                onActivated: customGalleryService.selectGallery(currentValue)
            }
            AppButton { text: "新建"; enabled: !root.state.busy; onClicked: root.askName("create", "我的路线") }
            AppButton { text: "复制"; enabled: !root.state.busy; onClicked: root.askName("copy", "我的路线副本") }
            AppButton { text: "改名"; enabled: !root.state.busy && !root.state.readonly; onClicked: root.askName("rename", galleries.currentText) }
            AppButton { text: "删除"; enabled: !root.state.busy && !root.state.readonly; onClicked: customGalleryService.requestDeleteGallery() }
            Item { Layout.fillWidth: true }
            AppButton {
                text: root.state.inUse === root.state.selectedGallery ? "停止使用" : "在游戏中使用"
                primary: true; enabled: root.state.canUse && root.state.available && !root.state.busy
                onClicked: customGalleryService.useGallery()
                ToolTip.visible: hovered && !root.state.canUse
                ToolTip.text: "运行时图集显示接口尚未接通"
            }
        }
        RowLayout {
            ComboBox {
                objectName: "customMapChoice"
                Layout.preferredWidth: 240
                model: root.state.maps; textRole: "name"; valueRole: "id"
                currentIndex: root.selectedIndex(root.state.maps, root.state.selectedMap)
                enabled: !root.state.busy && !root.state.pendingKind
                onActivated: customGalleryService.selectMap(currentValue)
            }
            ComboBox {
                Layout.preferredWidth: 110
                model: root.state.floors; textRole: "name"; valueRole: "id"
                currentIndex: root.selectedIndex(root.state.floors, root.state.selectedFloor)
                enabled: !root.state.busy && !root.state.pendingKind
                onActivated: customGalleryService.selectFloor(currentValue)
            }
            Repeater {
                model: root.state.colors
                delegate: Rectangle {
                    required property var modelData
                    width: 24; height: 24; radius: 12; color: modelData.value
                    border.color: root.state.color === modelData.value ? "#2765d7" : "#b9c9e1"
                    border.width: root.state.color === modelData.value ? 3 : 1
                    MouseArea { anchors.fill: parent; onClicked: customGalleryService.setColor(parent.modelData.value) }
                }
            }
            AppButton { text: root.state.newStart ? "新起点已选" : "新起点"; enabled: !root.state.readonly; onClicked: customGalleryService.toggleNewStart() }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            Rectangle {
                id: mapArea
                Layout.fillWidth: true; Layout.fillHeight: true
                radius: 12; color: "#e8eff9"; clip: true
                Image {
                    id: art
                    anchors.fill: parent; anchors.margins: 12
                    source: root.state.imageUrl || ""
                    asynchronous: true; cache: true; fillMode: Image.PreserveAspectFit
                    sourceSize.width: 1400
                    onStatusChanged: drawing.requestPaint()
                    onPaintedWidthChanged: drawing.requestPaint()
                    onPaintedHeightChanged: drawing.requestPaint()
                    Canvas {
                        id: drawing
                        objectName: "customRouteCanvas"
                        anchors.fill: parent
                        onPaint: {
                            var ctx=getContext("2d"); ctx.reset()
                            if (art.status !== Image.Ready || !root.state.imageWidth || !art.paintedWidth) return
                            var scale=art.paintedWidth/root.state.imageWidth
                            var ox=(width-art.paintedWidth)/2, oy=(height-art.paintedHeight)/2
                            ctx.translate(ox,oy); ctx.scale(scale,scale)
                            var lines=root.state.lines
                            for (var i=0; i<lines.length; ++i) {
                                var pts=lines[i].points; if (!pts.length) continue
                                ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y)
                                for (var j=1; j<pts.length; ++j) ctx.lineTo(pts[j].x,pts[j].y)
                                ctx.strokeStyle=lines[i].color; ctx.lineWidth=4/scale; ctx.lineJoin="round"; ctx.stroke()
                            }
                            for (var k=0; k<root.state.markers.length; ++k) {
                                var marker=root.state.markers[k]
                                ctx.beginPath(); ctx.arc(marker.x,marker.y,12/scale,0,Math.PI*2)
                                ctx.fillStyle="#14334b";ctx.fill();ctx.strokeStyle="#c6efff";ctx.lineWidth=1/scale;ctx.stroke()
                                ctx.font=(11/scale)+"px sans-serif";ctx.textAlign="center";ctx.textBaseline="middle"
                                ctx.fillStyle="white";ctx.fillText(marker.label,marker.x,marker.y)
                            }
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: !root.state.busy && !root.state.readonly && art.status === Image.Ready
                        onClicked: function(mouse) {
                            var ox=(width-art.paintedWidth)/2, oy=(height-art.paintedHeight)/2
                            if (mouse.x<ox || mouse.y<oy || mouse.x>width-ox || mouse.y>height-oy) return
                            customGalleryService.addPoint((mouse.x-ox)*root.state.imageWidth/art.paintedWidth,
                                (mouse.y-oy)*root.state.imageHeight/art.paintedHeight)
                        }
                    }
                }
                Label { anchors.centerIn: parent; width: parent.width-40; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: "#526785"; visible: !root.state.imageUrl; text: root.state.libraryMessage || "请选择一张地图" }
            }
            ColumnLayout {
                Layout.preferredWidth: 230; Layout.fillHeight: true; spacing: 8
                Label { text: "目标顺序"; color: "#183a73"; font.bold: true }
                Label { text: "点房间选目标，点道路加必经点。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                ListView {
                    id: points
                    objectName: "customPointList"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: root.state.points; spacing: 5
                    delegate: RowLayout {
                        required property int index
                        required property var modelData
                        width: points.width
                        Label { text: (index+1)+". "+modelData.floor+"楼 "+(modelData.start ? "起点 · " : "")+(modelData.kind === "room" ? "房间" : "必经点"); color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
                        AppButton { text: "↑"; implicitWidth: 28; enabled: index>0 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(parent.index,-1) }
                        AppButton { text: "↓"; implicitWidth: 28; enabled: index<root.state.points.length-1 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(parent.index,1) }
                        AppButton { text: "×"; implicitWidth: 28; enabled: !root.state.readonly && !root.state.busy; onClicked: customGalleryService.removePoint(parent.index) }
                    }
                }
                RowLayout {
                    AppButton { text: "撤销"; enabled: root.state.canUndo && !root.state.busy; onClicked: customGalleryService.undo() }
                    AppButton { text: "重做"; enabled: root.state.canRedo && !root.state.busy; onClicked: customGalleryService.redo() }
                    AppButton { text: "清空"; enabled: root.state.points.length>0 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.requestClearRoute() }
                }
            }
        }
        Label { text: root.state.busy ? "正在保存或规划…" : (root.state.message || root.state.planError || (root.state.readonly ? "默认图集只读，可创建副本后编辑" : "路线修改后自动保存")); wrapMode: Text.WordWrap; Layout.fillWidth: true; color: root.state.planError ? "#a34837" : "#526785" }
    }
    Dialog {
        id: nameDialog
        parent: root; anchors.centerIn: parent; width: 350; modal: true
        property string operation: ""
        title: operation === "export" ? "导出到测试数据目录" : "图集名称"
        standardButtons: Dialog.Ok | Dialog.Cancel
        TextField { id: nameField; objectName: "customGalleryName"; width: parent.width; maximumLength: nameDialog.operation === "export" ? 80 : 24; selectByMouse: true }
        onAccepted: {
            if (operation === "create") customGalleryService.createGallery(nameField.text)
            else if (operation === "copy") customGalleryService.copyGallery(nameField.text)
            else if (operation === "rename") customGalleryService.renameGallery(nameField.text)
            else if (operation === "export") customGalleryService.exportGallery(nameField.text)
        }
    }
    Dialog {
        id: confirmation
        parent: root; anchors.centerIn: parent; width: 360; modal: true
        closePolicy: Popup.NoAutoClose
        standardButtons: Dialog.Yes | Dialog.No
        Label { id: confirmationMessage; width: parent.width; wrapMode: Text.WordWrap; color: "#354f75" }
        onAccepted: customGalleryService.confirmOperation(true)
        onRejected: customGalleryService.confirmOperation(false)
    }
    FileDialog { id: importPicker; title: "导入自定义图集"; nameFilters: ["图集 JSON (*.json)"]; onAccepted: customGalleryService.importGallery(selectedFile.toString()) }
}
