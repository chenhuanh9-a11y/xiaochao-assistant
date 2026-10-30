import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    readonly property bool compact: width < 720
    property bool detailOpen: false
    property var scrollPositions: ({})
    property var detailViews: ({})
    readonly property var selectedProfile: {
        for (var n = 0; n < backend.profiles.length; n++)
            if (backend.profiles[n].id === backend.profileId) return backend.profiles[n]
        return ({id: "", name: "未选择作者", available: 0})
    }
    readonly property bool sameMode: backend.mode === session.currentMode
    readonly property bool currentAuthor: sameMode && backend.profileId === session.currentProfile
    readonly property bool canUseAuthor: sameMode && selectedProfile.available > 0 && !currentAuthor
        && !session.busy && !settingsService.busy && !resourceService.busy
    function floorLabel(value) { return value === "B1" ? "地下1层" : value ? value + "层" : "全部楼层" }
    function chooseLayer(key) { backend.select(key); if (page.compact) page.detailOpen = true }
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
        function onViewChanging(key) { page.scrollPositions[key] = gallery.contentY; page.detailViews[key] = page.detailOpen }
        function onViewChanged(key) {
            page.detailOpen = !!page.detailViews[key]
            Qt.callLater(function() { gallery.contentY = Math.min(page.scrollPositions[key] || 0, Math.max(0, gallery.contentHeight - gallery.height)) })
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: page.compact ? 8 : 12
        RowLayout {
            Layout.fillWidth: true
            AppButton { objectName: "galleryBackToList"; visible: page.compact && page.detailOpen; text: "返回列表"; onClicked: page.detailOpen = false }
            Label { text: page.compact && page.detailOpen ? "地图预览" : "地图资源"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "galleryRefresh"; text: "刷新"; implicitWidth: 64; enabled: !backend.busy && !resourceService.busy; onClicked: backend.refresh() }
            AppButton { objectName: "galleryManagePackages"; text: "管理图包"; onClicked: clientCoordinator.navigate("resources") }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            AppButton { objectName: "galleryNightmareMode"; text: "噩梦"; implicitWidth: 64; selected: backend.mode === "nightmare"; onClicked: backend.setMode("nightmare") }
            AppButton { objectName: "galleryDifficultMode"; text: "困难"; implicitWidth: 64; selected: backend.mode === "difficult"; onClicked: backend.setMode("difficult") }
            TextField {
                id: search; objectName: "clientGallerySearch"
                Layout.fillWidth: true; Layout.minimumWidth: 100
                placeholderText: "搜索地图"; selectByMouse: true
                onTextEdited: debounce.restart()
                Timer { id: debounce; interval: 140; onTriggered: backend.search(search.text) }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
            Rectangle {
                objectName: "galleryListPane"
                visible: !page.compact || !page.detailOpen
                // MapResourcesPage's existing list/detail columns use 5:4.
                Layout.preferredWidth: page.compact ? -1 : Math.round((page.width - 16) * 5 / 9)
                Layout.fillWidth: page.compact; Layout.fillHeight: true
                radius: 12; color: "white"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: page.compact ? 10 : 16; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true; visible: !page.compact
                        Label { text: "本地作者"; color: "#142b4b"; font.bold: true; font.pixelSize: 15 }
                        Item { Layout.fillWidth: true }
                        Label { text: backend.profiles.length + " 个来源"; color: "#526785"; font.pixelSize: 11 }
                    }
                    ListView {
                        id: authors; objectName: "galleryAuthorList"
                        visible: !page.compact
                        Layout.fillWidth: true; Layout.preferredHeight: Math.min(144, count * 48)
                        clip: true; model: backend.profiles; boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            id: author
                            required property var modelData
                            width: authors.width; height: 48; radius: 8
                            color: backend.profileId === modelData.id ? "#e7effc" : "transparent"
                            activeFocusOnTab: true; border.width: activeFocus ? 2 : 0; border.color: "#2f66eb"
                            Column {
                                anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 10
                                anchors.verticalCenter: parent.verticalCenter; spacing: 2
                                Label { width: parent.width; text: author.modelData.name; color: "#183a73"; font.bold: backend.profileId === author.modelData.id; elide: Text.ElideRight }
                                Label { text: author.modelData.available > 0 ? author.modelData.available + " 个本地图层" : "图像尚未安装"; color: "#526785"; font.pixelSize: 11 }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.setProfile(author.modelData.id) }
                            Keys.onReturnPressed: backend.setProfile(modelData.id)
                            Accessible.role: Accessible.Button; Accessible.name: modelData.name
                            Accessible.onPressAction: backend.setProfile(modelData.id)
                        }
                    }
                    RowLayout {
                        visible: page.compact; Layout.fillWidth: true
                        ComboBox {
                            objectName: "galleryCompactAuthorPicker"
                            Layout.fillWidth: true; model: backend.profiles; textRole: "name"; valueRole: "id"
                            currentIndex: { var profile = backend.profileId; return indexOfValue(profile) }
                            onActivated: backend.setProfile(currentValue)
                        }
                        ComboBox {
                            objectName: "galleryCompactFloorPicker"; Layout.preferredWidth: 110
                            model: [{name:"全部楼层",value:""},{name:"1层",value:"1"},{name:"2层",value:"2"},{name:"地下1层",value:"B1"}]
                            textRole: "name"; valueRole: "value"
                            currentIndex: { var floor = backend.floor; return indexOfValue(floor) }
                            onActivated: backend.setFloor(currentValue)
                        }
                    }
                    Rectangle { visible: !page.compact; Layout.fillWidth: true; height: 1; color: "#e3e8f1" }
                    RowLayout {
                        visible: !page.compact; Layout.fillWidth: true
                        Label { text: "地图与楼层"; color: "#142b4b"; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: backend.count + " 个图层"; color: "#526785"; font.pixelSize: 11 }
                    }
                    ListView {
                        id: gallery; objectName: "clientResourceGrid"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; reuseItems: true; cacheBuffer: 136; model: resourceModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            id: row
                            required property int index
                            required property string key
                            required property string title
                            required property string floor
                            required property string variant
                            required property string imageUrl
                            required property bool available
                            objectName: "galleryLayer_" + index
                            width: gallery.width; height: 68; radius: 8
                            color: backend.selectedKey === key ? "#e7effc" : "transparent"
                            activeFocusOnTab: true; border.width: activeFocus ? 2 : 0; border.color: "#2f66eb"
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 8; spacing: 10
                                Rectangle {
                                    Layout.preferredWidth: 52; Layout.preferredHeight: 48; radius: 6; color: "#edf2fa"
                                    Image { anchors.fill: parent; anchors.margins: 3; source: row.imageUrl; sourceSize: Qt.size(104,96); asynchronous: true; fillMode: Image.PreserveAspectFit }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 3
                                    Label { Layout.fillWidth: true; text: row.title; color: "#142b4b"; font.bold: backend.selectedKey === row.key; elide: Text.ElideRight }
                                    Label { Layout.fillWidth: true; text: page.floorLabel(row.floor) + " · " + row.variant; color: "#526785"; font.pixelSize: 11; elide: Text.ElideRight }
                                    Label { visible: !row.available; text: "图像未安装"; color: "#9a5b0f"; font.pixelSize: 11 }
                                }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.chooseLayer(row.key) }
                            Keys.onReturnPressed: page.chooseLayer(key)
                            Accessible.role: Accessible.Button; Accessible.name: title + " " + page.floorLabel(floor) + " " + variant
                            Accessible.onPressAction: page.chooseLayer(key)
                        }
                        Label {
                            anchors.centerIn: parent; width: parent.width - 12; visible: backend.count === 0
                            text: backend.busy ? "正在读取本地资源…" : "没有符合条件的图层\n可以调整搜索或导入图包"
                            color: "#526785"; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
            }
            Rectangle {
                objectName: "galleryPreviewPane"
                visible: !page.compact || page.detailOpen
                Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "white"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: page.compact ? 10 : 16; spacing: 8
                    Label { visible: !page.compact; text: page.selectedProfile.name + " · 预览"; color: "#142b4b"; font.pixelSize: 17; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                    ComboBox {
                        id: layerPicker; objectName: "galleryLayerPicker"
                        Layout.fillWidth: true; model: resourceModel; textRole: "title"; valueRole: "key"
                        currentIndex: { var key = backend.selectedKey; return indexOfValue(key) }
                        displayText: currentIndex < 0 ? "选择地图、楼层与路线版本" : currentText + " · " + page.floorLabel(backend.selectedFloor)
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
                        id: floorPicker; objectName: "galleryFloorPicker"; Layout.preferredWidth: 110
                        model: [{name:"全部楼层",value:""},{name:"1层",value:"1"},{name:"2层",value:"2"},{name:"地下1层",value:"B1"}]
                        textRole: "name"; valueRole: "value"
                        currentIndex: { var floor = backend.floor; return indexOfValue(floor) }
                        onActivated: backend.setFloor(currentValue)
                    }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 64
                        color: "#edf2fa"; radius: 8
                        Image {
                            id: preview; objectName: "galleryLargePreview"
                            anchors.fill: parent; anchors.margins: 12
                            source: backend.selectedImage; sourceSize: Qt.size(1000,900); asynchronous: true; fillMode: Image.PreserveAspectFit
                        }
                        Label {
                            anchors.centerIn: parent; width: parent.width - 24; visible: preview.status !== Image.Ready
                            text: !backend.selectedKey ? "从左侧选择地图图层" : !backend.selectedImage ? "该图层图像尚未安装\n可在图包管理中导入" : preview.status === Image.Error ? "预览读取失败，可刷新后重试" : "正在读取预览…"
                            color: "#526785"; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#d6e0ef" }
        ColumnLayout {
            objectName: "gallerySelectionFooter"
            Layout.fillWidth: true; spacing: 6
            Label { text: "当前游戏路线：" + page.activeAuthorName(); color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                AppButton { objectName: "galleryUseAuthor"; text: page.currentAuthor ? "正在使用此作者" : "使用此作者"; primary: true; enabled: page.canUseAuthor; onClicked: clientCoordinator.setRouteProfile(backend.profileId) }
                AppButton { objectName: "galleryShowReference"; text: "打开定点预览"; enabled: backend.selectedImage !== ""; onClicked: clientCoordinator.showReference() }
                Item { Layout.fillWidth: true }
                Label { visible: !page.compact; text: page.sameMode ? "浏览与预览不会自动更换作者" : "浏览范围与工作模式不同"; color: "#526785"; font.pixelSize: 11 }
            }
            Label { text: backend.message; color: backend.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11 }
        }
    }
}
