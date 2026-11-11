import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    readonly property bool compact: width < 720
    readonly property var hostWindow: Window.window
    readonly property bool contentActive: visible && hostWindow !== null && hostWindow.visible && hostWindow.visibility !== Window.Minimized
    property bool detailOpen: false
    property var scrollPositions: ({})
    property var detailViews: ({})
    property var packageActions: null
    property var packageStatusOwner: null
    readonly property bool packageOperationPending: !packageActions || packageActions.operationPending || packageActions.recoveryPending
    readonly property bool supportsPackageImport: backend.season === "s0" || backend.season === "s1"
    readonly property bool canImportPackage: supportsPackageImport && !packageOperationPending
    Component.onCompleted: { backend.setActive(contentActive); if (contentActive) { backend.clearGallerySearch(); backend.selectFirstLayer() } }
    onContentActiveChanged: { backend.setActive(contentActive); if (contentActive) { backend.clearGallerySearch(); backend.selectFirstLayer() } }
    readonly property var selectedProfile: {
        for (var n = 0; n < backend.profiles.length; n++)
            if (backend.profiles[n].id === backend.profileId) return backend.profiles[n]
        return ({id: "", name: "选择一个图包", packageId: "", available: 0, availableMaps: 0, mapCount: 0})
    }
    readonly property var selectedPackage: {
        if (!packageActions || !packageActions.packageManager || backend.busy || backend.season !== "s0" || !selectedProfile.packageId) return null
        var rows = packageActions.packageManager.packageRows
        for (var n = 0; n < rows.length; n++)
            if (rows[n].packageId === selectedProfile.packageId && rows[n].mode === backend.mode) return rows[n]
        return null
    }
    readonly property bool canRemovePackage: !!selectedPackage && (!!selectedPackage.canRemove || !!selectedPackage.canInspectRemoval) && !packageOperationPending
    function importPackage() {
        if (!canImportPackage) return
        packageStatusOwner = resourceService
        packageActions.openImport(backend.season)
    }
    function removeSelectedPackage() {
        if (!canRemovePackage) return
        packageStatusOwner = packageActions.packageManager
        packageActions.removePackage(selectedPackage.packageId)
    }
    readonly property bool sameMode: backend.mode === session.currentMode && backend.season === session.currentSeason
    readonly property string selectedRuntimeProfile: selectedProfile.runtimeProfileId || selectedProfile.id || ""
    readonly property bool selectableAuthor: selectedProfile.runtimeSelectable === true
    readonly property bool currentAuthor: sameMode && selectedRuntimeProfile === session.currentProfile
    readonly property bool canUseAuthor: selectableAuthor && sameMode && selectedProfile.available > 0 && !currentAuthor
        && !backend.busy
        && !session.busy && !settingsService.busy && !resourceService.busy
    function floorLabel(value) {
        var options = backend.floorOptions || []
        for (var i = 0; i < options.length; i++) if (options[i].value === value) return options[i].name
        if (value && value.indexOf("source-height-") === 0) return "源高度 " + value.substring(14)
        return value === "B1" ? "B1 地下室" : value ? value + " 楼" : "全部楼层"
    }
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
                if ((backend.profiles[n].runtimeProfileId || backend.profiles[n].id) === session.currentProfile) return backend.profiles[n].name
        }
        return session.currentProfile ? "当前工作模式已保存的路线" : "未选择"
    }
    Connections {
        target: backend
        function onChanged() {
            if (backend.hasError && !backend.busy) page.packageStatusOwner = null
            if (page.contentActive && !backend.busy && !backend.selectedKey) backend.selectFirstLayer()
        }
        function onViewChanging(key) {
            page.packageStatusOwner = null
            page.scrollPositions[key] = authors.contentY; page.detailViews[key] = page.detailOpen
        }
        function onViewChanged(key) {
            if (page.contentActive) backend.selectFirstLayer()
            page.detailOpen = !!page.detailViews[key]
            Qt.callLater(function() { authors.contentY = Math.min(page.scrollPositions[key] || 0, Math.max(0, authors.contentHeight - authors.height)) })
        }
    }
    Connections {
        target: page.hostWindow
        function onActiveFocusItemChanged() {
            if (page.hostWindow) detailScroll.revealFocusedItem(page.hostWindow.activeFocusItem)
        }
    }
    Connections {
        target: page.packageActions
        function onImportSelectionCancelled() {
            if (page.packageStatusOwner === resourceService) page.packageStatusOwner = null
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: page.compact ? 8 : 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "地图资源"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "galleryRefresh"; text: "刷新本机"; enabled: !backend.busy && !resourceService.busy; onClicked: { page.packageStatusOwner = null; backend.refresh() } }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            SeasonSelector {
                id: galleryContextSelector; objectName: "galleryContext"
                seasons: settingsService.seasonOptions
                season: backend.season; mode: backend.mode
                // Retired ordinary mode is not a usable gallery entry.
                browseDifficulty2: false
                enabled: true
                onContextRequested: function(season, mode) { backend.setContext(season, mode) }
            }
            Label { objectName: "galleryMapCount"; visible: !page.compact; text: "仅浏览 · " + backend.mapCount + " 份图"; color: "#526785"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "galleryManagePackages"; text: "图库管理"; onClicked: clientCoordinator.navigate("resources") }
        }
        ClientPackageProgress {
            Layout.fillWidth: true
            localService: resourceService
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
                    clip: true; reuseItems: true
                    model: backend.galleryProfileModel
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Item {
                        id: author
                        required property var modelData
                        width: authors.cellWidth; height: authors.cellHeight
                        Rectangle {
                            visible: !author.modelData.importCard
                            anchors.fill: parent; anchors.rightMargin: authors.columns === 2 ? 8 : 0; anchors.bottomMargin: 10
                            radius: 12; color: backend.profileId === author.modelData.id ? "#e7effc" : "white"
                            border.width: backend.profileId === author.modelData.id || authorTap.activeFocus ? 2 : 1
                            border.color: backend.profileId === author.modelData.id || authorTap.activeFocus ? "#2f66eb" : "#d6e0ef"
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 12; spacing: 6
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 112; color: "#edf1f7"; radius: 6
                                    Image { anchors.fill: parent; anchors.margins: 5; source: author.modelData.previewUrl || ""; sourceSize: Qt.size(360,240); asynchronous: true; cache: !source.toString().startsWith("image://importedgallery/"); fillMode: Image.PreserveAspectFit }
                                    Label { anchors.centerIn: parent; width: parent.width - 12; visible: !author.modelData.previewUrl; text: "安装后可预览作者图"; color: "#526785"; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                                }
                                Label { Layout.fillWidth: true; text: author.modelData.name; textFormat: Text.PlainText; color: "#183a73"; font.bold: true; elide: Text.ElideRight }
                                Label { text: author.modelData.userProvided ? "作者配准 · 尚未复核" : author.modelData.availableMaps > 0 ? "本地已装" : "图包尚未安装"; color: "#526785"; font.pixelSize: 11 }
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
                        Button {
                            id: importCard; objectName: author.modelData.importCard ? "galleryImportCard" : ""
                            visible: !!author.modelData.importCard
                            anchors.fill: parent; anchors.rightMargin: authors.columns === 2 ? 8 : 0; anchors.bottomMargin: 10
                            enabled: page.canImportPackage
                            hoverEnabled: true; focusPolicy: Qt.StrongFocus
                            Accessible.name: "导入 " + galleryContextSelector.seasonLabel(backend.season) + " ZIP 图包"
                            onClicked: page.importPackage()
                            HoverHandler { cursorShape: importCard.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                            background: Rectangle {
                                radius: 12
                                color: importCard.down ? "#e0e9fd" : importCard.hovered ? "#f0f5ff" : "#f8faff"
                                border.width: importCard.activeFocus ? 2 : 1
                                border.color: importCard.activeFocus ? "#2f66eb" : "#c3d2eb"
                            }
                            contentItem: Item {
                                Column {
                                    anchors.centerIn: parent; width: parent.width; spacing: 8
                                    Label { width: parent.width; text: "+"; font.pixelSize: 46; color: importCard.enabled ? "#2f66eb" : "#8797b1"; horizontalAlignment: Text.AlignHCenter }
                                    Label { width: parent.width; text: "导入 ZIP 图包"; font.bold: true; color: "#354f75"; horizontalAlignment: Text.AlignHCenter }
                                    Label {
                                        width: parent.width; leftPadding: 12; rightPadding: 12
                                        text: !page.supportsPackageImport ? galleryContextSelector.seasonLabel(backend.season) + " 暂不支持作者图包导入"
                                            : backend.busy && backend.profiles.length === 0 ? "正在读取地图资源…"
                                            : page.packageActions && page.packageActions.recoveryPending ? "请先在图库管理中完成上次操作"
                                            : "点击选择本地 ZIP 文件"
                                        color: "#526785"; font.pixelSize: 11; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                objectName: "galleryPreviewPane"
                visible: !page.compact || page.detailOpen
                Layout.preferredWidth: page.compact ? page.width : Math.round((page.width - 12) * 4 / 9)
                Layout.fillWidth: true; Layout.fillHeight: true; radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                    Flickable {
                        id: detailScroll; objectName: "galleryDetailScroll"
                        function revealFocusedItem(item) {
                            if (!page.contentActive || !item) return
                            if (item.focusReason !== Qt.TabFocusReason && item.focusReason !== Qt.BacktabFocusReason) return
                            var ancestor = item
                            while (ancestor && ancestor !== detailContents) ancestor = ancestor.parent
                            if (ancestor !== detailContents) return
                            var top = item.mapToItem(contentItem, 0, 0).y
                            var next = contentY
                            if (top < contentY) next = top
                            else if (top + item.height > contentY + height) next = top + item.height - height
                            contentY = Math.max(0, Math.min(Math.max(0, contentHeight - height), next))
                        }
                        anchors.fill: parent; anchors.margins: 16; clip: true
                        contentWidth: width; contentHeight: detailContents.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        ColumnLayout {
                            id: detailContents; width: detailScroll.width; spacing: 8
                    RowLayout {
                        id: detailHeading
                        Layout.fillWidth: true; spacing: 8
                        AppButton { id: detailBack; objectName: "galleryBackToList"; visible: page.compact && page.detailOpen; text: "返回列表"; onClicked: page.detailOpen = false }
                        Label { id: detailTitle; text: page.selectedProfile.name + " · 预览"; textFormat: Text.PlainText; color: "#142b4b"; font.pixelSize: 17; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(140, detailScroll.height
                            - detailHeading.implicitHeight - detailPickers.implicitHeight - detailCoverage.implicitHeight
                            - detailUseAuthor.implicitHeight - detailActions.implicitHeight - detailDisclaimer.implicitHeight
                            - detailContents.spacing * 6)
                        color: "#edf1f7"; radius: 8
                        Image {
                            id: preview; objectName: "galleryLargePreview"
                            anchors.fill: parent; anchors.margins: 12
                            source: backend.selectedImage; sourceSize: Qt.size(1000,900); asynchronous: true; cache: !source.toString().startsWith("image://importedgallery/"); fillMode: Image.PreserveAspectFit
                        }
                        Label {
                            anchors.centerIn: parent; width: parent.width - 24; visible: preview.status !== Image.Ready
                            text: !backend.selectedKey ? "选择地图和楼层预览" : !backend.selectedImage ? "此地图图片尚未安装\n可在图库管理中导入" : preview.status === Image.Error ? "预览读取失败，请刷新后重试" : "正在读取预览…"
                            color: "#526785"; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        }
                    }
                    RowLayout {
                        id: detailPickers
                        Layout.fillWidth: true; spacing: 6
                        ComboBox {
                            id: layerPicker; objectName: "galleryLayerPicker"
                            Layout.fillWidth: true; Layout.minimumWidth: 80; model: resourceModel; textRole: "title"; valueRole: "key"
                            currentIndex: backend.selectedRow
                            displayText: currentIndex < 0 ? "选择地图" : currentText + " · " + page.floorLabel(backend.selectedFloor)
                            delegate: ItemDelegate {
                                id: layerOption
                                required property var model
                                required property int index
                                objectName: "galleryLayerOption_" + index
                                width: layerPicker.width
                                text: model.title + " · " + page.floorLabel(model.floor) + " · " + model.variant
                                contentItem: Text {
                                    text: layerOption.text; textFormat: Text.PlainText; font: layerOption.font
                                    color: layerOption.highlighted ? layerOption.palette.highlightedText : layerOption.palette.buttonText
                                    elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                                }
                                onClicked: { page.chooseLayer(model.key); layerPicker.popup.close() }
                            }
                            onActivated: page.chooseLayer(currentValue)
                        }
                        ComboBox {
                            id: floorPicker; objectName: "galleryFloorPicker"; Layout.preferredWidth: 100
                            model: backend.floorOptions
                            textRole: "name"; valueRole: "value"
                            currentIndex: {
                                for (var n = 0; n < model.length; n++) if (model[n].value === backend.floor) return n
                                return 0
                            }
                            onActivated: backend.setFloor(currentValue)
                        }
                    }
                    Label { id: detailCoverage; text: "本地覆盖 " + page.selectedProfile.availableMaps + " / " + page.selectedProfile.mapCount + " 份图"; color: "#526785"; font.pixelSize: 11 }
                    AppButton { id: detailUseAuthor; objectName: "galleryUseAuthor"; text: !page.selectableAuthor ? "此图包仅供预览" : !page.sameMode ? "请先切换游戏赛季与难度" : page.currentAuthor ? "正在使用此作者" : "使用此作者"; Layout.fillWidth: true; primary: true; enabled: page.canUseAuthor; onClicked: clientCoordinator.useGalleryAuthor(backend.season, backend.mode, backend.profileId) }
                    RowLayout {
                        id: detailActions
                        Layout.fillWidth: true; spacing: 8
                        AppButton { objectName: "galleryShowReference"; text: "打开独立预览"; enabled: backend.selectedImage !== ""; onClicked: backend.showOverlay() }
                        Item { Layout.fillWidth: true }
                        AppButton {
                            objectName: "galleryRemovePackage"; text: "删除此图包"; enabled: page.canRemovePackage
                            Accessible.name: "删除图包 " + page.selectedProfile.name
                            ToolTip.visible: hovered
                            ToolTip.text: page.selectedPackage ? "核对并确认后删除此图包；未知或改动过的文件会保留。" : "内置资源或未登记归属的图包不可删除。"
                            onClicked: page.removeSelectedPackage()
                        }
                    }
                    Label { id: detailDisclaimer; text: "作者图仅用于显示，不参与识别。预览不会自动更换作者。"; color: "#526785"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                }
            }
        }
        ColumnLayout {
            objectName: "gallerySelectionFooter"
            Layout.fillWidth: true; spacing: 6
            Label { text: "当前游戏路线：" + page.activeAuthorName(); color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { objectName: "galleryOperationMessage"; text: page.packageStatusOwner ? page.packageStatusOwner.message : backend.message; color: backend.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11 }
        }
    }
}
