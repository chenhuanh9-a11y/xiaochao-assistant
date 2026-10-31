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
    readonly property bool editing: state.editing || false
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
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            AppButton { objectName: "customBackToGalleries"; visible: root.editing; text: "返回图库"; enabled: !root.state.busy && !root.state.pendingKind; onClicked: customGalleryService.browseGalleries() }
            Label { text: root.editing ? root.state.mapName || "地图编辑" : "自定义图库"; color: "#142b4b"; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
            ComboBox {
                visible: root.editing
                objectName: "customMapChoice"
                Layout.preferredWidth: 180
                model: root.state.maps; textRole: "name"; valueRole: "id"
                currentIndex: root.selectedIndex(root.state.maps, root.state.selectedMap)
                enabled: !root.state.busy && !root.state.pendingKind
                onActivated: customGalleryService.selectMap(currentValue)
            }
            AppButton { text: "刷新"; objectName: "customRefresh"; enabled: !root.state.busy; onClicked: customGalleryService.refresh() }
        }
        Label { visible: !root.editing; text: "选择图库，再点地图排好路线。修改会自动保存。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            visible: !root.editing
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            Rectangle {
                Layout.preferredWidth: 180; Layout.minimumWidth: 180; Layout.maximumWidth: 180; Layout.fillHeight: true
                color: "white"; radius: 12; border.color: "#d6e0ef"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 10
                    Label { text: "图库"; color: "#183a73"; font.pixelSize: 15; font.bold: true; padding: 4 }
                    ListView {
                        id: galleries
                        objectName: "customGalleryChoice"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; spacing: 4; model: root.state.galleries
                        ScrollBar.vertical: ScrollBar {}
                        delegate: AppButton {
                            required property var modelData
                            width: galleries.width; text: modelData.name; selected: modelData.id === root.state.selectedGallery
                            Accessible.description: modelData.readonly ? "只读图库，可复制后编辑" : "可编辑图库"
                            enabled: !root.state.busy && !root.state.pendingKind
                            onClicked: customGalleryService.selectGallery(modelData.id)
                        }
                    }
                    GridLayout {
                        columns: 2; columnSpacing: 6; rowSpacing: 6; Layout.fillWidth: true
                        AppButton { text: "新建"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !root.state.dataError; onClicked: root.askName("create", "我的路线") }
                        AppButton { text: "复制"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !root.state.dataError; onClicked: root.askName("copy", (root.state.galleryName || "我的路线") + "副本") }
                        AppButton { text: "改名"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !root.state.readonly; onClicked: root.askName("rename", root.state.galleryName) }
                        AppButton { objectName: "customDeleteGallery"; text: "删除"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !!root.state.canDelete; onClicked: customGalleryService.requestDeleteGallery() }
                        AppButton { text: "导入"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy; onClicked: importPicker.open() }
                        AppButton { objectName: "customExportGallery"; text: "导出"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !root.state.pendingKind; onClicked: exportPicker.open() }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true
                color: "white"; radius: 12; border.color: "#d6e0ef"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: root.state.galleryName || "图库"; font.pixelSize: 18; font.bold: true; color: "#142b4b"; Layout.fillWidth: true; elide: Text.ElideRight }
                        AppButton {
                            text: root.state.inUse === root.state.selectedGallery ? "停止使用" : "在游戏中使用"
                            primary: true; enabled: root.state.canUse && root.state.available && !root.state.busy
                            onClicked: customGalleryService.useGallery()
                        }
                    }
                    Label { text: "已绘制 " + (root.state.drawnCount || 0) + " / " + (root.state.mapCount || 0) + " 张地图" + (root.state.readonly ? " · 默认图库只读，复制后即可编辑" : " · 选择地图继续编辑"); color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    GridView {
                        id: maps
                        objectName: "customMapGrid"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; model: root.state.maps
                        cellWidth: Math.max(152, Math.floor(width / Math.max(1, Math.floor(width / 152))))
                        cellHeight: 184; cacheBuffer: 368
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Button {
                            id: tile
                            objectName: "customMapCard_" + modelData.id
                            required property var modelData
                            width: maps.cellWidth - 8; height: maps.cellHeight - 8
                            focusPolicy: Qt.StrongFocus; hoverEnabled: true
                            Accessible.name: modelData.name + (modelData.drawn ? "，已绘制" : "，未绘制")
                            enabled: !root.state.busy && !root.state.pendingKind
                            background: Rectangle { radius: 8; color: tile.down ? "#dce8ff" : tile.hovered ? "#e7effc" : "#f3f6fc"; border.color: tile.activeFocus ? "#2f66eb" : "#d6e0ef"; border.width: tile.activeFocus ? 2 : 1 }
                            contentItem: Column {
                                spacing: 4
                                Image { width: 132; height: 132; anchors.horizontalCenter: parent.horizontalCenter; source: tile.modelData.thumbnailUrl; asynchronous: true; cache: true; fillMode: Image.PreserveAspectFit; sourceSize.width: 264; sourceSize.height: 264 }
                                Text { width: parent.width; text: tile.modelData.name; color: "#142b4b"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
                                Text { width: parent.width; text: tile.modelData.thumbnailError ? "资源待检查" : tile.modelData.drawn ? "已绘制" : "未绘制"; color: tile.modelData.drawn ? "#17775a" : "#526785"; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter }
                            }
                            onClicked: customGalleryService.selectMap(modelData.id)
                        }
                    }
                    Label { visible: !root.state.mapCount; text: root.state.libraryMessage || "自定义地图资源尚未安装"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
            }
        }
        RowLayout {
            visible: root.editing
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true
                color: "white"; radius: 12; border.color: "#d6e0ef"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        ComboBox {
                            objectName: "customFloorChoice"
                            Layout.preferredWidth: 84
                            model: root.state.floors; textRole: "name"; valueRole: "id"
                            currentIndex: root.selectedIndex(root.state.floors, root.state.selectedFloor)
                            enabled: !root.state.busy && !root.state.pendingKind
                            onActivated: customGalleryService.selectFloor(currentValue)
                        }
                        Repeater {
                            model: root.state.colors
                            delegate: Button {
                                id: swatch
                                required property var modelData
                                implicitWidth: 28; implicitHeight: 32
                                focusPolicy: Qt.StrongFocus; hoverEnabled: true
                                Accessible.name: "路线颜色：" + modelData.name
                                ToolTip.visible: hovered; ToolTip.text: modelData.name
                                enabled: !root.state.busy && !root.state.pendingKind && !root.state.readonly
                                contentItem: Item { Rectangle { anchors.centerIn: parent; width: 18; height: 18; radius: 9; color: swatch.modelData.value; border.color: "#8da4c4" } }
                                background: Rectangle { radius: 6; color: swatch.hovered ? "#e7effc" : "transparent"; border.color: swatch.activeFocus || root.state.color === swatch.modelData.value ? "#2f66eb" : "transparent"; border.width: 2 }
                                onClicked: customGalleryService.setColor(modelData.value)
                            }
                        }
                        Item { Layout.fillWidth: true }
                        AppButton { text: root.state.newStart ? "取消新起点" : "新起点"; implicitWidth: 86; enabled: !root.state.readonly && !root.state.busy; onClicked: customGalleryService.toggleNewStart() }
                    }
            Rectangle {
                id: mapArea
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: 720; Layout.minimumWidth: 200
                radius: 12; color: "#e8eff9"; clip: true
                Image {
                    id: art
                    objectName: "customMapImage"
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
                            if (art.status !== Image.Ready || root.state.imageWidth <= 0 || art.paintedWidth <= 0 || art.paintedHeight <= 0 || width <= 0 || height <= 0) return
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
                        objectName: "customMapDrawingSurface"
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
                }
            }
            Rectangle {
                Layout.preferredWidth: root.width < 1000 ? 200 : 230; Layout.minimumWidth: 190; Layout.maximumWidth: 230
                Layout.fillHeight: true; color: "white"; radius: 12; border.color: "#d6e0ef"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 8
                    Label { text: "目标顺序"; color: "#183a73"; font.pixelSize: 15; font.bold: true }
                    Label { text: "点房间选择目标，点走廊添加必经点。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    ListView {
                        id: points
                        objectName: "customPointList"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                        model: root.state.points; spacing: 8
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ColumnLayout {
                            required property int index
                            required property var modelData
                            width: points.width; spacing: 4
                            Label { text: (parent.index+1)+". "+parent.modelData.floor+"层 "+(parent.modelData.start ? "起点 · " : "")+(parent.modelData.kind === "room" ? "房间" : "必经点"); color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
                            RowLayout {
                                AppButton { text: "上移"; implicitWidth: 48; implicitHeight: 30; enabled: parent.parent.index>0 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(parent.parent.index,-1) }
                                AppButton { text: "下移"; implicitWidth: 48; implicitHeight: 30; enabled: parent.parent.index<root.state.points.length-1 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(parent.parent.index,1) }
                                AppButton { text: "移除"; implicitWidth: 48; implicitHeight: 30; enabled: !root.state.readonly && !root.state.busy; onClicked: customGalleryService.removePoint(parent.parent.index) }
                            }
                        }
                    }
                    RowLayout {
                        AppButton { text: "撤销"; Layout.fillWidth: true; implicitWidth: 70; enabled: root.state.canUndo && !root.state.busy; onClicked: customGalleryService.undo() }
                        AppButton { text: "重做"; Layout.fillWidth: true; implicitWidth: 70; enabled: root.state.canRedo && !root.state.busy; onClicked: customGalleryService.redo() }
                    }
                    AppButton { text: "清空路线"; Layout.fillWidth: true; enabled: root.state.points.length>0 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.requestClearRoute() }
                    Label { visible: root.state.readonly; text: "默认图库只读，复制后即可绘制。"; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    AppButton { visible: root.state.readonly; text: "复制后编辑"; Layout.fillWidth: true; enabled: !root.state.busy && !root.state.dataError; onClicked: root.askName("copy", (root.state.galleryName || "路线") + "副本") }
                }
            }
        }
        Label { text: root.state.busy ? "正在保存或规划…" : (root.state.message || root.state.planError || (root.state.readonly ? "默认图库只读，可复制后编辑" : "路线修改后自动保存")); wrapMode: Text.WordWrap; Layout.fillWidth: true; color: root.state.planError ? "#a34837" : "#526785" }
    }
    Dialog {
        id: nameDialog
        parent: root; anchors.centerIn: parent; width: 350; modal: true
        property string operation: ""
        title: "图集名称"
        standardButtons: Dialog.Ok | Dialog.Cancel
        TextField { id: nameField; objectName: "customGalleryName"; width: parent.width; maximumLength: 24; selectByMouse: true }
        onAccepted: {
            if (operation === "create") customGalleryService.createGallery(nameField.text)
            else if (operation === "copy") customGalleryService.copyGallery(nameField.text)
            else if (operation === "rename") customGalleryService.renameGallery(nameField.text)
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
    FileDialog {
        id: exportPicker
        objectName: "customExportDialog"
        title: "导出图库"
        fileMode: FileDialog.SaveFile
        currentFolder: customGalleryService.exportDirectoryUrl
        defaultSuffix: "json"
        nameFilters: ["星图图库 (*.json)"]
        onAccepted: customGalleryService.exportGalleryTo(selectedFile.toString())
    }
    FileDialog { id: importPicker; title: "导入自定义图集"; nameFilters: ["图集 JSON (*.json)"]; onAccepted: customGalleryService.importGallery(selectedFile.toString()) }
}
