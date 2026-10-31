import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    readonly property bool compact: width < 720
    property bool detailOpen: false
    property var scrollPositions: ({})
    property var detailViews: ({})
    Component.onCompleted: { backend.clearGallerySearch(); backend.selectFirstLayer() }
    onVisibleChanged: if (visible) { backend.clearGallerySearch(); backend.selectFirstLayer() }
    readonly property var selectedProfile: {
        for (var n = 0; n < backend.profiles.length; n++)
            if (backend.profiles[n].id === backend.profileId) return backend.profiles[n]
        return ({id: "", name: "选择一个图包", available: 0, availableMaps: 0, mapCount: 0})
    }
    readonly property bool sameMode: backend.mode === session.currentMode
    readonly property bool currentAuthor: sameMode && backend.profileId === session.currentProfile
    readonly property bool canUseAuthor: sameMode && selectedProfile.available > 0 && !currentAuthor
        && !session.busy && !settingsService.busy && !resourceService.busy
    function floorLabel(value) { return value === "B1" ? "B1 地下室" : value ? value + " 楼" : "全部楼层" }
    function chooseAuthor(id) {
        backend.setProfile(id)
        backend.selectFirstLayer()
        if (page.compact) page.detailOpen = true
    }
    function chooseLayer(key) { backend.select(key) }
    function activeAuthorName() {
        if (session.currentProfile === "shared-structure") return "结构底图"
        if (page.sameMode) {
            for (var n = 0; n < backend.profiles.length; n++)
                if (backend.profiles[n].id === session.currentProfile) return backend.profiles[n].name
        }
        return session.currentProfile ? "当前工作模式已保存的路线" : "未选择"
    }
    Connections {
        target: backend
        function onChanged() {
            if (page.visible && !backend.busy && !backend.selectedKey) backend.selectFirstLayer()
        }
        function onViewChanging(key) { page.scrollPositions[key] = authors.contentY; page.detailViews[key] = page.detailOpen }
        function onViewChanged(key) {
            if (page.visible) backend.selectFirstLayer()
            page.detailOpen = !!page.detailViews[key]
            Qt.callLater(function() { authors.contentY = Math.min(page.scrollPositions[key] || 0, Math.max(0, authors.contentHeight - authors.height)) })
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: page.compact ? 8 : 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "地图资源"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "galleryRefresh"; text: "刷新本机"; enabled: !backend.busy && !resourceService.busy; onClicked: backend.refresh() }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            AppButton { objectName: "galleryNightmareMode"; text: "噩梦"; implicitWidth: 64; selected: backend.mode === "nightmare"; onClicked: backend.setMode("nightmare") }
            AppButton { objectName: "galleryDifficultMode"; text: "困难"; implicitWidth: 64; selected: backend.mode === "difficult"; onClicked: backend.setMode("difficult") }
            Label { objectName: "galleryMapCount"; text: "仅浏览 · " + backend.mapCount + " 份图"; color: "#526785"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "galleryManagePackages"; text: "图库管理"; onClicked: clientCoordinator.navigate("resources") }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            Item {
                objectName: "galleryListPane"
                visible: !page.compact || !page.detailOpen
                Layout.preferredWidth: page.compact ? page.width : Math.round((page.width - 12) * 5 / 9)
                Layout.fillWidth: page.compact; Layout.fillHeight: true
                GridView {
                    id: authors; objectName: "galleryAuthorGrid"
                    anchors.fill: parent
                    readonly property int columns: width >= 420 ? 2 : 1
                    cellWidth: width / columns; cellHeight: 250
                    clip: true; reuseItems: true; model: backend.profiles
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Item {
                        id: author
                        required property var modelData
                        width: authors.cellWidth; height: authors.cellHeight
                        Rectangle {
                            anchors.fill: parent; anchors.rightMargin: authors.columns === 2 ? 8 : 0; anchors.bottomMargin: 10
                            radius: 12; color: backend.profileId === author.modelData.id ? "#e7effc" : "white"
                            border.width: backend.profileId === author.modelData.id || authorTap.activeFocus ? 2 : 1
                            border.color: backend.profileId === author.modelData.id || authorTap.activeFocus ? "#2f66eb" : "#d6e0ef"
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 12; spacing: 6
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 112; color: "#edf1f7"; radius: 6
                                    Image { anchors.fill: parent; anchors.margins: 5; source: author.modelData.previewUrl || ""; sourceSize: Qt.size(360,240); asynchronous: true; fillMode: Image.PreserveAspectFit }
                                    Label { anchors.centerIn: parent; width: parent.width - 12; visible: !author.modelData.previewUrl; text: "安装后可预览作者图"; color: "#526785"; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                                }
                                Label { Layout.fillWidth: true; text: author.modelData.name; color: "#183a73"; font.bold: true; elide: Text.ElideRight }
                                Label { text: author.modelData.availableMaps > 0 ? "本地已装" : "图包尚未安装"; color: "#526785"; font.pixelSize: 11 }
                                Label { text: author.modelData.availableMaps + " / " + author.modelData.mapCount + " 份图"; color: "#526785"; font.pixelSize: 11 }
                                Item { Layout.fillHeight: true }
                                AppButton { objectName: "galleryViewAuthor_" + author.modelData.id; text: "查看图包"; onClicked: page.chooseAuthor(author.modelData.id) }
                            }
                            MouseArea {
                                id: authorTap; objectName: "galleryAuthor_" + author.modelData.id
                                anchors.fill: parent; anchors.bottomMargin: 48
                                activeFocusOnTab: true; cursorShape: Qt.PointingHandCursor
                                onClicked: page.chooseAuthor(author.modelData.id)
                                Keys.onReturnPressed: page.chooseAuthor(author.modelData.id)
                                Accessible.role: Accessible.Button; Accessible.name: author.modelData.name
                                Accessible.onPressAction: page.chooseAuthor(author.modelData.id)
                            }
                        }
                    }
                    Label {
                        anchors.centerIn: parent; width: parent.width - 24; visible: backend.profiles.length === 0
                        text: backend.busy ? "正在读取地图资源…" : "暂无地图资源，可在图库管理中导入。"
                        color: "#526785"; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
            Rectangle {
                objectName: "galleryPreviewPane"
                visible: !page.compact || page.detailOpen
                Layout.preferredWidth: page.compact ? page.width : Math.round((page.width - 12) * 4 / 9)
                Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "white"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                    Flickable {
                        id: detailScroll; objectName: "galleryDetailScroll"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                        contentWidth: width; contentHeight: detailContents.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        ColumnLayout {
                            id: detailContents; width: detailScroll.width; spacing: 8
                    AppButton { objectName: "galleryBackToList"; visible: page.compact && page.detailOpen; text: "返回列表"; onClicked: page.detailOpen = false }
                    Label { text: page.selectedProfile.name + " · 预览"; color: "#142b4b"; font.pixelSize: 17; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: Math.max(140, detailScroll.height - (page.compact ? 140 : 100))
                        color: "#edf1f7"; radius: 8
                        Image {
                            id: preview; objectName: "galleryLargePreview"
                            anchors.fill: parent; anchors.margins: 12
                            source: backend.selectedImage; sourceSize: Qt.size(1000,900); asynchronous: true; fillMode: Image.PreserveAspectFit
                        }
                        Label {
                            anchors.centerIn: parent; width: parent.width - 24; visible: preview.status !== Image.Ready
                            text: !backend.selectedKey ? "选择地图和楼层预览" : !backend.selectedImage ? "此地图图片尚未安装\n可在图库管理中导入" : preview.status === Image.Error ? "预览读取失败，请刷新后重试" : "正在读取预览…"
                            color: "#526785"; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        ComboBox {
                            id: layerPicker; objectName: "galleryLayerPicker"
                            Layout.fillWidth: true; Layout.minimumWidth: 80; model: resourceModel; textRole: "title"; valueRole: "key"
                            currentIndex: backend.selectedRow
                            displayText: currentIndex < 0 ? "选择地图" : currentText + " · " + page.floorLabel(backend.selectedFloor)
                            delegate: ItemDelegate {
                                required property var model
                                required property int index
                                objectName: "galleryLayerOption_" + index
                                width: layerPicker.width
                                text: model.title + " · " + page.floorLabel(model.floor) + " · " + model.variant
                                onClicked: { page.chooseLayer(model.key); layerPicker.popup.close() }
                            }
                            onActivated: page.chooseLayer(currentValue)
                        }
                        ComboBox {
                            id: floorPicker; objectName: "galleryFloorPicker"; Layout.preferredWidth: 100
                            model: [{name:"全部楼层",value:""},{name:"1 楼",value:"1"},{name:"2 楼",value:"2"},{name:"B1 地下室",value:"B1"}]
                            textRole: "name"; valueRole: "value"
                            currentIndex: backend.floor === "1" ? 1 : backend.floor === "2" ? 2 : backend.floor === "B1" ? 3 : 0
                            onActivated: backend.setFloor(currentValue)
                        }
                    }
                    Label { text: "本地覆盖 " + page.selectedProfile.availableMaps + " / " + page.selectedProfile.mapCount + " 份图"; color: "#526785"; font.pixelSize: 11 }
                        }
                    }
                    AppButton { objectName: "galleryUseAuthor"; text: page.currentAuthor ? "正在使用此作者" : "使用此作者"; Layout.fillWidth: true; primary: true; enabled: page.canUseAuthor; onClicked: clientCoordinator.setRouteProfile(backend.profileId) }
                    AppButton { objectName: "galleryShowReference"; text: "打开独立预览"; enabled: backend.selectedImage !== ""; onClicked: clientCoordinator.showReference() }
                    Label { text: "预览不会自动更换作者。"; color: "#526785"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
            }
        }
        ColumnLayout {
            objectName: "gallerySelectionFooter"
            Layout.fillWidth: true; spacing: 6
            Label { text: "当前游戏路线：" + page.activeAuthorName(); color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: backend.message; color: backend.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11 }
        }
    }
}
