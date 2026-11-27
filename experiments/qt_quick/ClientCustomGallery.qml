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
    readonly property bool showPointNumbers: settingsService && settingsService.persistedValues.routePointNumbers || false
    readonly property var routeLength: state.routeLength || ({})
    readonly property var pointSegments: routeLength.pointSegments || []
    property int selectedSegmentEnd: -1
    property string lastSelectionKey: ""
    readonly property var selectedSegment: (selectedSegmentEnd >= 0
        && selectedSegmentEnd < pointSegments.length
        && pointSegments[selectedSegmentEnd]
        && pointSegments[selectedSegmentEnd].hasSegment) ? pointSegments[selectedSegmentEnd] : null
    onShowPointNumbersChanged: drawing.requestPaint()
    onSelectedSegmentEndChanged: drawing.requestPaint()
    function selectedIndex(rows, id) {
        for (var i=0; i<rows.length; ++i) if (rows[i].id === id) return i
        return -1
    }
    function floorLabel(id) {
        var rows = root.state.floors || []
        for (var i=0; i<rows.length; ++i) if (rows[i].id === id) return rows[i].name
        return id + "层"
    }
    function comparisonText(rows) {
        if (!rows || !rows.length) return ""
        var parts = []
        var limit = Math.min(rows.length, 3)
        for (var i = 0; i < limit; ++i) {
            var item = rows[i]
            parts.push(item.galleryName + " " + (item.error ? "待规划" : (item.totalText || "0 格")))
        }
        if (rows.length > limit) parts.push("等 " + rows.length + " 个图库")
        return "同图参考：" + parts.join(" · ")
    }
    function segmentBreakdownText(seg) {
        if (!seg) return ""
        var title = "已选第 " + seg.fromNumber + "→" + seg.toNumber + " 段：" + (seg.text || "0 格")
        if (seg.crossFloor) {
            var fl = seg.floors || []
            if (fl.length > 0) {
                var detail = []
                for (var i = 0; i < fl.length; ++i) detail.push(fl[i].name + " " + fl[i].text)
                return title + "（" + detail.join(" + ") + "，跨层不计）"
            }
            return title + "（跨层跳转不计）"
        }
        return title
    }
    function syncSegmentSelection() {
        var key = (root.state.scopeId || "") + "|" + (root.state.selectedGallery || "") + "|" + (root.state.selectedMap || "")
        if (key !== root.lastSelectionKey) {
            root.lastSelectionKey = key
            root.selectedSegmentEnd = -1
            return
        }
        if (root.selectedSegmentEnd >= 0) {
            var segs = root.pointSegments
            if (root.selectedSegmentEnd >= segs.length || !segs[root.selectedSegmentEnd] || !segs[root.selectedSegmentEnd].hasSegment) {
                root.selectedSegmentEnd = -1
            }
        }
    }
    function askName(operation, initial) {
        nameDialog.operation = operation
        nameField.text = initial
        nameDialog.open()
        nameField.forceActiveFocus()
    }
    Connections {
        target: customGalleryService
        function onChanged() {
            root.syncSegmentSelection()
            drawing.requestPaint()
        }
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
        RowLayout {
            Layout.fillWidth: true
            Label { text: "场景与难度"; color: "#526785" }
            ComboBox {
                objectName: "customRouteScope"
                Layout.fillWidth: true
                model: root.state.scopeOptions || []
                textRole: "name"; valueRole: "id"
                currentIndex: root.selectedIndex(root.state.scopeOptions || [], root.state.scopeId)
                enabled: !root.state.busy && !root.state.pendingKind
                onActivated: customGalleryService.selectScope(currentValue)
            }
        }
        Label { visible: !root.editing; text: "选择图库，再点地图排好路线。修改会自动保存。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            visible: root.editing; Layout.fillWidth: true
            RoutePointNumbersSwitch {}
            Label { text: "每层从 1 开始 · 重复选点合并显示"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        }
        Label { visible: settingsService && (settingsService.loadFailed || settingsService.message.indexOf("失败") >= 0); text: settingsService ? settingsService.message : ""; color: "#b91c1c"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            visible: !root.editing
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            Rectangle {
                Layout.preferredWidth: 180; Layout.minimumWidth: 180; Layout.maximumWidth: 180; Layout.fillHeight: true
                color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
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
                        AppButton { text: "删除"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !!root.state.canDelete; onClicked: customGalleryService.requestDeleteGallery() }
                        AppButton { text: "导入"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy; onClicked: importPicker.open() }
                        AppButton { objectName: "customExportGallery"; text: "导出"; Layout.fillWidth: true; implicitWidth: 74; enabled: !root.state.busy && !root.state.pendingKind; onClicked: exportPicker.open() }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true
                color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: root.state.galleryName || "图库"; font.pixelSize: 18; font.bold: true; color: "#142b4b"; Layout.fillWidth: true; elide: Text.ElideRight }
                        AppButton {
                            text: root.state.inUse === root.state.selectedGallery ? "停止使用" : "在游戏中使用"
                            primary: true; enabled: root.state.canUse && root.state.available && !root.state.busy
                            onClicked: customGalleryService.useGallery()
                        }
                    }
                    Label { text: "已绘制 " + (root.state.drawnCount || 0) + " / " + (root.state.mapCount || 0) + " 张地图" + (root.state.source ? " · 来自路线广场（作者 " + (root.state.source.author || "匿名") + "），只读，复制后即可编辑" : root.state.readonly ? " · 默认图库只读，复制后即可编辑" : " · 选择地图继续编辑"); color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Label {
                        objectName: "customGalleryLengthHint"
                        text: "长度单位「格」：" + (root.state.routeLengthUnitDescription || "按本图比例估算，仅用于同一张图内比较（跨层不计）")
                        color: "#6b7f9c"; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    GridView {
                        id: maps
                        objectName: "customMapGrid"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; model: root.state.maps
                        cellWidth: Math.max(156, Math.floor(width / Math.max(1, Math.floor(width / 156))))
                        cellHeight: 198; cacheBuffer: 396
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Button {
                            id: tile
                            objectName: "customMapCard_" + modelData.id
                            required property var modelData
                            width: maps.cellWidth - 8; height: maps.cellHeight - 8
                            focusPolicy: Qt.StrongFocus; hoverEnabled: true
                            ToolTip.visible: hovered && !!modelData.routeLengthTooltip
                            ToolTip.delay: 300
                            ToolTip.text: modelData.routeLengthTooltip || ""
                            Accessible.name: modelData.name + (modelData.drawn ? ("，已绘制，" + (modelData.routeLengthText || "0 格")) : "，未绘制")
                            enabled: !root.state.busy && !root.state.pendingKind
                            background: Rectangle { radius: 8; color: tile.down ? "#dce8ff" : tile.hovered ? "#e7effc" : "#f3f6fc"; border.color: tile.activeFocus ? "#2f66eb" : "#d6e0ef"; border.width: tile.activeFocus ? 2 : 1 }
                            contentItem: Column {
                                spacing: 3
                                Image { width: 126; height: 126; anchors.horizontalCenter: parent.horizontalCenter; source: tile.modelData.thumbnailUrl; asynchronous: true; cache: true; fillMode: Image.PreserveAspectFit; sourceSize.width: 264; sourceSize.height: 264 }
                                Text { width: parent.width; text: tile.modelData.name; color: "#142b4b"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
                                Text {
                                    objectName: "customMapCardLength_" + tile.modelData.id
                                    width: parent.width
                                    text: tile.modelData.thumbnailError
                                          ? "资源待检查"
                                          : tile.modelData.drawn
                                            ? (tile.modelData.routeLengthError ? "已绘制 · 路径待调整" : ("已绘制 · " + (tile.modelData.routeLengthText || "0 格")))
                                            : "未绘制"
                                    color: tile.modelData.routeLengthError ? "#a34837" : tile.modelData.drawn ? "#17775a" : "#526785"
                                    font.pixelSize: 11; font.bold: !!tile.modelData.drawn && !tile.modelData.routeLengthError
                                    horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                }
                                Text {
                                    objectName: "customMapCardFloors_" + tile.modelData.id
                                    visible: !!tile.modelData.drawn && !!tile.modelData.routeLengthFloorText
                                    width: parent.width
                                    text: tile.modelData.routeLengthFloorText || ""
                                    color: "#526785"; font.pixelSize: 10
                                    horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                }
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
                color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 8
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
                        AppButton {
                            text: root.state.newStart ? "取消新起点" : "新起点"; implicitWidth: 86
                            enabled: !root.state.readonly && !root.state.busy
                            ToolTip.visible: hovered; ToolTip.delay: 300
                            ToolTip.text: "从下一个点开始另起一段独立路线（跨楼层画线时也可先点此按钮）"
                            onClicked: customGalleryService.toggleNewStart()
                        }
                    }
                    Rectangle {
                        objectName: "customRouteLengthPanel"
                        Layout.fillWidth: true
                        implicitHeight: lengthCol.implicitHeight + 12
                        radius: 10; color: "#f4f8ff"; border.width: 1; border.color: "#dbe6f7"
                        ColumnLayout {
                            id: lengthCol
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 6; anchors.bottomMargin: 6
                            spacing: 4
                            Flow {
                                Layout.fillWidth: true
                                spacing: 6
                                Rectangle {
                                    height: 24; width: totalLbl.implicitWidth + 14; radius: 6
                                    color: root.routeLength.error ? "#fdecea" : "#183a73"
                                    Label {
                                        id: totalLbl
                                        objectName: "customRouteTotalLength"
                                        anchors.centerIn: parent
                                        text: (root.routeLength.hasMultiFloorRoute ? "多层总长 " : "路线总长 ") + (root.routeLength.totalText || "0 格")
                                        color: root.routeLength.error ? "#a34837" : "white"
                                        font.pixelSize: 12; font.bold: true
                                    }
                                }
                                Repeater {
                                    model: root.routeLength.floors || []
                                    delegate: Rectangle {
                                        id: floorChip
                                        required property var modelData
                                        readonly property bool activeFloor: modelData.id === root.state.selectedFloor
                                        height: 24; width: floorChipLbl.implicitWidth + 14; radius: 6
                                        color: activeFloor ? "#dce8ff" : "white"
                                        border.width: 1; border.color: activeFloor ? "#2f66eb" : "#d6e0ef"
                                        Label {
                                            id: floorChipLbl
                                            objectName: "customFloorLength_" + floorChip.modelData.id
                                            anchors.centerIn: parent
                                            text: floorChip.modelData.name + " " + floorChip.modelData.text
                                            color: floorChip.activeFloor ? "#142b4b" : (floorChip.modelData.hasLength ? "#354f75" : "#6b7f9c")
                                            font.pixelSize: 11; font.bold: floorChip.activeFloor || !!floorChip.modelData.hasLength
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            enabled: !root.state.busy && !root.state.pendingKind && !floorChip.activeFloor
                                            onClicked: customGalleryService.selectFloor(floorChip.modelData.id)
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Label {
                                    objectName: "customRouteLengthHint"
                                    text: root.routeLength.unitDescription || "按本图比例估算，仅用于同一张图内比较（跨层不计）"
                                    color: "#5e7494"; font.pixelSize: 11
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    objectName: "customRouteComparisons"
                                    visible: (root.state.mapComparisons || []).length > 0
                                    text: root.comparisonText(root.state.mapComparisons)
                                    color: "#183a73"; font.pixelSize: 11; font.bold: true
                                    elide: Text.ElideRight
                                    Layout.maximumWidth: 320
                                }
                            }
                            Rectangle {
                                objectName: "customSelectedSegmentBar"
                                visible: root.selectedSegment !== null
                                Layout.fillWidth: true
                                implicitHeight: segRow.implicitHeight + 6
                                radius: 6; color: "#fff8db"; border.width: 1; border.color: "#ebd075"
                                RowLayout {
                                    id: segRow
                                    anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 6; anchors.topMargin: 3; anchors.bottomMargin: 3
                                    spacing: 6
                                    Label {
                                        objectName: "customSelectedSegmentText"
                                        text: root.segmentBreakdownText(root.selectedSegment)
                                        color: "#6b4d00"; font.pixelSize: 11; font.bold: true
                                        Layout.fillWidth: true; elide: Text.ElideRight
                                    }
                                    AppButton {
                                        objectName: "customClearSelectedSegment"
                                        text: "取消"
                                        implicitWidth: 44; implicitHeight: 22
                                        onClicked: root.selectedSegmentEnd = -1
                                    }
                                }
                            }
                        }
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
                            if (root.selectedSegmentEnd >= 0) {
                                ctx.save()
                                ctx.globalAlpha = 0.85
                                for (var h=0; h<lines.length; ++h) {
                                    if (lines[h].segmentEnd !== root.selectedSegmentEnd) continue
                                    var hpts=lines[h].points; if (!hpts.length) continue
                                    ctx.beginPath(); ctx.moveTo(hpts[0].x,hpts[0].y)
                                    for (var hj=1; hj<hpts.length; ++hj) ctx.lineTo(hpts[hj].x,hpts[hj].y)
                                    ctx.strokeStyle="#ffd54f"
                                    ctx.lineWidth=11.5/scale
                                    ctx.lineJoin="round"; ctx.lineCap="round"; ctx.stroke()
                                }
                                ctx.restore()
                            }
                            // Same as custom_maps.draw_route and the Android route: every
                            // dark outline first, then the colours, so crossings keep colour.
                            for (var pass=0; pass<2; ++pass) {
                                for (var i=0; i<lines.length; ++i) {
                                    var pts=lines[i].points; if (!pts.length) continue
                                    ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y)
                                    for (var j=1; j<pts.length; ++j) ctx.lineTo(pts[j].x,pts[j].y)
                                    ctx.strokeStyle=pass === 0 ? "#0f1b28" : lines[i].color
                                    ctx.lineWidth=(pass === 0 ? 6.8 : 4)/scale
                                    ctx.lineJoin="round"; ctx.lineCap="round"; ctx.stroke()
                                }
                            }
                            if (!root.showPointNumbers) return
                            for (var k=0; k<root.state.markers.length; ++k) {
                                var marker=root.state.markers[k]
                                ctx.font="bold "+(13/scale)+"px sans-serif";ctx.textAlign="center";ctx.textBaseline="middle"
                                var half=Math.max(12/scale,ctx.measureText(marker.label).width/2+6/scale)
                                var mx=Math.max(half, Math.min(root.state.imageWidth-half,marker.x))
                                var my=Math.max(13/scale,marker.y-28/scale)
                                ctx.fillStyle="#14334b";ctx.fillRect(mx-half,my-11/scale,half*2,22/scale)
                                ctx.strokeStyle="#c6efff";ctx.lineWidth=1/scale;ctx.strokeRect(mx-half,my-11/scale,half*2,22/scale)
                                ctx.fillStyle="white";ctx.fillText(marker.label,mx,my)
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
                Layout.preferredWidth: root.width < 1000 ? 200 : 236; Layout.minimumWidth: 196; Layout.maximumWidth: 236
                Layout.fillHeight: true; color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 8
                    Label { text: "目标顺序"; color: "#183a73"; font.pixelSize: 15; font.bold: true }
                    Label { text: "点房间选择目标，点走廊添加必经点。点段长可高亮对应路段。"; color: "#526785"; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    ListView {
                        id: points
                        objectName: "customPointList"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                        model: root.state.points; spacing: 6
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            id: pointCard
                            required property int index
                            required property var modelData
                            readonly property var segInfo: (index >= 0 && index < root.pointSegments.length) ? root.pointSegments[index] : null
                            readonly property bool selectable: !!(segInfo && segInfo.hasSegment)
                            readonly property bool isSelected: selectable && root.selectedSegmentEnd === index
                            width: points.width
                            implicitHeight: pointCol.implicitHeight + 8
                            radius: 8
                            color: isSelected ? "#eef4ff" : "transparent"
                            border.width: isSelected ? 1 : 0
                            border.color: "#2f66eb"
                            ColumnLayout {
                                id: pointCol
                                anchors.fill: parent; anchors.margins: 4; spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 4
                                    Label {
                                        text: (pointCard.index+1)+". "+root.floorLabel(pointCard.modelData.floor)+" "+(pointCard.modelData.start ? "起点 · " : "")+(pointCard.modelData.kind === "room" ? "房间" : "必经点")
                                        color: "#354f75"; font.pixelSize: 12
                                        Layout.fillWidth: true; elide: Text.ElideRight
                                    }
                                    Rectangle {
                                        objectName: "customPointSegmentBadge_" + pointCard.index
                                        visible: pointCard.selectable
                                        height: 20; width: segBadgeLbl.implicitWidth + 10; radius: 5
                                        color: pointCard.isSelected ? "#2f66eb" : "#e8f0fc"
                                        Label {
                                            id: segBadgeLbl
                                            anchors.centerIn: parent
                                            text: pointCard.segInfo
                                                  ? (pointCard.segInfo.crossFloor && pointCard.segInfo.units <= 0
                                                     ? "跨层"
                                                     : ("+" + pointCard.segInfo.text))
                                                  : ""
                                            color: pointCard.isSelected ? "white" : "#183a73"
                                            font.pixelSize: 10; font.bold: true
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: root.selectedSegmentEnd = (root.selectedSegmentEnd === pointCard.index ? -1 : pointCard.index)
                                        }
                                    }
                                }
                                RowLayout {
                                    AppButton { text: "上移"; implicitWidth: 48; implicitHeight: 28; enabled: pointCard.index>0 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(pointCard.index,-1) }
                                    AppButton { text: "下移"; implicitWidth: 48; implicitHeight: 28; enabled: pointCard.index<root.state.points.length-1 && !root.state.readonly && !root.state.busy; onClicked: customGalleryService.movePoint(pointCard.index,1) }
                                    AppButton { text: "移除"; implicitWidth: 48; implicitHeight: 28; enabled: !root.state.readonly && !root.state.busy; onClicked: customGalleryService.removePoint(pointCard.index) }
                                }
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
        defaultSuffix: selectedNameFilter.index === 1 ? "xingtu-gallery" : "json"
        nameFilters: ["星图图库 (*.json)", "安卓路线包 (*.xingtu-gallery)"]
        onAccepted: customGalleryService.exportGalleryTo(selectedFile.toString())
    }
    FileDialog { id: importPicker; title: "导入自定义图集"; nameFilters: ["星图图库与路线包 (*.json *.xingtu-gallery *.zip)"]; onAccepted: customGalleryService.importGallery(selectedFile.toString()) }
}
