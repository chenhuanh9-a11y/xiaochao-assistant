import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    objectName: "clientSettings"
    // SettingsService is supplied by the client as the settingsService context property.
    readonly property var preferences: settingsService.values
    property var profileOptions: []
    property string profilesMode: ""
    property string profilesSeason: ""
    property bool profilesBusy: false
    readonly property string draftSeason: preferences.gameSeason || "s0"
    readonly property bool automaticMapSelection: draftSeason === "s1"
    readonly property string draftContextKey: draftSeason + ":" + preferences.gameMode
    readonly property string draftProfile: (preferences.contextRouteProfiles || {})[draftContextKey]
        || (draftSeason === "s0" ? ((preferences.modeRouteProfiles || {})[preferences.gameMode] || preferences.routeProfileId || "") : "")
    readonly property bool profilesCurrent: !profilesBusy && profilesMode === preferences.gameMode && profilesSeason === draftSeason
    readonly property var actionNames: ["显示/隐藏面板", "上一个候选", "下一个候选", "确认本局", "切换楼层", "结束本局", "显示/隐藏小抄", "开始本局", "开始/结束本局", "手动选图"]
    readonly property var actionIds: ["toggle-panel", "candidate-prev", "candidate-next", "confirm-round", "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round", "manual-map-picker"]
    readonly property var moduleNames: ({display: "显示", mode: "游戏模式", manual_map: "选择地图与楼层", recognition: "候选与再识别", round: "本局动作"})
    property bool advancedShortcuts: false
    property bool advancedDisplay: false
    property bool modeExpanded: false
    property bool modulesExpanded: false
    property bool referenceEditRequested: false
    readonly property bool referenceEditing: typeof clientCoordinator !== "undefined" && clientCoordinator.referenceEditing === true
    readonly property bool layoutDirty: settingsService.controlLayoutDirty
    function manualMapAction(target) {
        return ["candidate-prev", "candidate-next", "confirm-round", "manual-map-picker"].indexOf(target) >= 0
    }
    onAutomaticMapSelectionChanged: {
        // Settings changes notify all bindings; drain that notification before
        // cancelling a recorder, and recheck after a rapid return to S0.
        Qt.callLater(function() {
            if (root.automaticMapSelection && settingsService.shortcutRecording
                    && root.manualMapAction(settingsService.shortcutTarget)) settingsService.cancelShortcutCapture()
        })
    }
    function recordShortcut(target) {
        if (automaticMapSelection && manualMapAction(target)) return
        if (settingsService.beginShortcutCapture(target)) captureArea.forceActiveFocus()
    }
    function saveMapSelectionMode(mode) {
        if (automaticMapSelection) return
        settingsService.setValue("mapSelectionMode", mode)
        settingsService.saveFields(["mapSelectionMode"])
    }
    function shortcutLabel(target) {
        var index = actionIds.indexOf(target)
        return target === "hotkey" ? "地图键" : index >= 0 ? actionNames[index] : "快捷键"
    }
    onVisibleChanged: if (!visible) settingsService.cancelShortcutCapture()

    function setAction(index, text) {
        var keys = preferences.actionHotkeys.slice()
        keys[index] = text
        return settingsService.setValue("actionHotkeys", keys)
    }
    function setLayoutFlag(section, key, flag) {
        var layout = JSON.parse(JSON.stringify(preferences.gameControlLayout))
        if (section === "display") layout.display[key] = flag
        else if (flag && layout.enabled.indexOf(key) < 0) layout.enabled.push(key)
        else if (!flag) layout.enabled = layout.enabled.filter(function(item) { return item !== key })
        settingsService.setValue("gameControlLayout", layout)
    }

    component Heading: Label {
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 17
        font.bold: true
        color: "#142b4b"
        Layout.topMargin: 16
        Layout.bottomMargin: 2
    }
    component Note: Label {
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 14
        color: "#526785"
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    component Field: TextField {
        selectByMouse: true
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 13
        color: "#354963"
        implicitHeight: 38
        padding: 10
        background: Rectangle {
            color: "white"
            radius: 8
            border.color: parent.activeFocus ? "#2f66eb" : "#d6e0ef"
            border.width: parent.activeFocus ? 2 : 1
        }
    }

    component ShortcutRow: RowLayout {
        id: keyRow
        required property int actionIndex
        required property string title
        visible: !root.automaticMapSelection || [1, 2, 3, 9].indexOf(actionIndex) < 0
        enabled: visible
        Layout.fillWidth: true
        Layout.minimumHeight: 54
        spacing: 8
        Label {
            text: keyRow.title
            color: "#183a73"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13
            Layout.fillWidth: true; Layout.minimumWidth: 84
            wrapMode: Text.WordWrap
        }
        Field {
            objectName: keyRow.actionIndex < 0 ? "setting_hotkey" : "setting_action_" + keyRow.actionIndex
            Layout.preferredWidth: 104; Layout.maximumWidth: 132
            readOnly: true
            enabled: !settingsService.shortcutRecording
            text: keyRow.actionIndex < 0 ? settingsService.persistedValues.hotkey : settingsService.persistedValues.actionHotkeys[keyRow.actionIndex]
            placeholderText: "未绑定"
            horizontalAlignment: Text.AlignHCenter
            Accessible.name: keyRow.title + "快捷键"
        }
        AppButton {
            objectName: keyRow.actionIndex < 0 ? "record_hotkey" : "record_action_" + keyRow.actionIndex
            text: "录入"; implicitWidth: 58; padding: 8
            enabled: !settingsService.busy
            Accessible.name: "录入" + keyRow.title + "快捷键"
            onClicked: root.recordShortcut(keyRow.actionIndex < 0 ? "hotkey" : root.actionIds[keyRow.actionIndex])
        }
    }
    component OpacityBlock: ColumnLayout {
        id: opacityBlock
        required property string settingKey
        required property string title
        property real minimum: 0
        property real maximum: 100
        Layout.fillWidth: true
        spacing: 14
        Layout.topMargin: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: opacityBlock.title; color: "#183a73"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; Layout.fillWidth: true }
            Label { text: Math.round(root.preferences[opacityBlock.settingKey] * 100) + "%"; color: "#235ce9"; font.pixelSize: 13 }
        }
        AppSlider {
            objectName: "setting_" + opacityBlock.settingKey
            Layout.fillWidth: true
            from: opacityBlock.minimum; to: opacityBlock.maximum; stepSize: 1
            value: Math.round(root.preferences[opacityBlock.settingKey] * 100)
            Accessible.name: opacityBlock.title
            onMoved: settingsService.setValue(opacityBlock.settingKey, value / 100)
        }
    }
    component Disclosure: AppButton {
        property bool expanded: false
        selected: expanded
        Layout.alignment: Qt.AlignLeft
        implicitHeight: 34
        font.pixelSize: 12
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 18
        RowLayout {
            Layout.fillWidth: true
            Label { text: "设置"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 32; font.bold: true }
            Item { Layout.fillWidth: true }
            Label { text: settingsService.busy ? "正在保存…" : settingsService.dirty ? "未保存" : "已同步"; color: "#526785"; font.pixelSize: 12 }
        }
        ColumnLayout {
            visible: typeof gameStartupService !== "undefined"
            Layout.fillWidth: true
            spacing: 4
            CheckBox {
                objectName: "gameStartupToggle"
                text: "游戏启动时启动星图"
                checked: typeof gameStartupService !== "undefined" && gameStartupService.enabled
                enabled: typeof gameStartupService !== "undefined" && (gameStartupService.available || gameStartupService.enabled)
                onClicked: {
                    gameStartupService.setEnabled(checked)
                    checked = Qt.binding(function() { return gameStartupService.enabled })
                }
            }
            Note {
                text: typeof gameStartupService !== "undefined" ? gameStartupService.message : ""
            }
            Note {
                text: "启动星图时需确认管理员权限。"
            }
        }
        Rectangle {
            visible: settingsService.shortcutRecording
            z: 1  // Keep the recorder above scroll content before the next layout pass.
            Layout.fillWidth: true
            implicitHeight: captureContents.implicitHeight + 24
            radius: 8; color: "#e7effc"
            ColumnLayout {
                id: captureContents
                anchors.fill: parent; anchors.margins: 12
                spacing: 8
                Label { text: "录入：" + root.shortcutLabel(settingsService.shortcutTarget); color: "#142b4b"; font.pixelSize: 14; font.bold: true }
                FocusScope {
                    id: captureArea
                    objectName: "shortcutCaptureArea"
                    Layout.fillWidth: true
                    implicitHeight: 40
                    activeFocusOnTab: true
                    Keys.priority: Keys.BeforeItem
                    Keys.onPressed: function(event) {
                        if (!event.isAutoRepeat && settingsService.captureShortcutKey(event.key, event.modifiers, event.text, false)) captureConfirm.forceActiveFocus()
                        event.accepted = true
                    }
                    Keys.onReleased: function(event) {
                        if (!event.isAutoRepeat && settingsService.captureShortcutKey(event.key, event.modifiers, event.text, true)) captureConfirm.forceActiveFocus()
                        event.accepted = true
                    }
                    Rectangle { anchors.fill: parent; color: "white"; radius: 8; border.color: captureArea.activeFocus ? "#2f66eb" : "#cad8ee"; border.width: captureArea.activeFocus ? 2 : 1 }
                    Label { anchors.centerIn: parent; text: settingsService.shortcutCandidateReady ? (settingsService.shortcutCandidate || "未绑定") : "在此按键，或按鼠标右键 / 中键 / 侧键"; color: "#183a73"; font.pixelSize: 13 }
                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton | Qt.BackButton | Qt.ForwardButton
                        onPressed: function(mouse) {
                            if (mouse.button === Qt.LeftButton) captureArea.forceActiveFocus()
                            else if (settingsService.captureShortcutMouse(mouse.button, mouse.modifiers)) captureConfirm.forceActiveFocus()
                        }
                    }
                }
                Note { text: settingsService.shortcutMessage }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    AppButton { id: captureConfirm; objectName: "shortcutCaptureConfirm"; text: settingsService.busy ? "正在保存…" : "保存并生效"; primary: true; enabled: settingsService.shortcutCandidateReady && !settingsService.busy; onClicked: settingsService.confirmShortcutCapture() }
                    AppButton { objectName: "shortcutCaptureAgain"; enabled: !settingsService.busy; text: "重新录入"; onClicked: root.recordShortcut(settingsService.shortcutTarget) }
                    AppButton { objectName: "shortcutCaptureClear"; enabled: !settingsService.busy; text: "解绑"; onClicked: settingsService.clearShortcutCandidate() }
                    AppButton { objectName: "shortcutCaptureCancel"; enabled: !settingsService.busy; text: "取消"; onClicked: settingsService.cancelShortcutCapture() }
                }
            }
        }
        ScrollView {
            id: scroll
            objectName: "settingsScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Rectangle {
                id: settingsCard
                objectName: "settingsMainCard"
                width: scroll.availableWidth
                implicitHeight: cardContents.implicitHeight + (root.width < 680 ? 40 : 64)
                height: implicitHeight
                radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                ColumnLayout {
                    id: cardContents
                    anchors.top: parent.top; anchors.topMargin: root.width < 680 ? 20 : 32
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(parent.width - (root.width < 680 ? 40 : 64), 1280)
                    spacing: 18
                    GridLayout {
                        objectName: "settingsMainColumns"
                        Layout.fillWidth: true
                        columns: root.width >= 720 ? 2 : 1
                        uniformCellWidths: true
                        columnSpacing: root.width >= 1100 ? 72 : 32
                        rowSpacing: 30
                        ColumnLayout {
                            objectName: "settingsShortcutColumn"
                            Layout.fillWidth: true; Layout.maximumWidth: 560
                            Layout.alignment: Qt.AlignTop
                            spacing: 10
                            Heading { text: "快捷键"; Layout.topMargin: 0; Layout.bottomMargin: 16 }
                            ShortcutRow { actionIndex: -1; title: "游戏地图键" }
                            ShortcutRow { actionIndex: 0; title: "游戏控件" }
                            ShortcutRow { actionIndex: 8; title: "本局开始 / 结束" }
                            ShortcutRow { actionIndex: 6; title: "小抄显影" }
                            Disclosure { objectName: "advancedShortcutsToggle"; text: root.advancedShortcuts ? "收起高级快捷键" : "高级快捷键"; expanded: root.advancedShortcuts; onClicked: root.advancedShortcuts = !root.advancedShortcuts }
                            ColumnLayout {
                                visible: root.advancedShortcuts
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: [1, 2, 3, 4, 5, 7, 9]
                                    delegate: ShortcutRow {
                                        required property int modelData
                                        actionIndex: modelData
                                        title: root.actionNames[modelData]
                                    }
                                }
                            }
                            Note { text: "点击录入后按键并确认；保存后生效。清空可解绑。" }
                            Disclosure { objectName: "controlLayoutToggle"; text: root.modulesExpanded ? "收起悬浮面板控件" : "自定义悬浮面板控件"; expanded: root.modulesExpanded; onClicked: root.modulesExpanded = !root.modulesExpanded }
                            ColumnLayout {
                                visible: root.modulesExpanded
                                Layout.fillWidth: true
                                spacing: 10
                                Note { text: "勾选显示的模块，用上移 / 下移调整顺序。修改会预览到游戏控制面板，保存布局后保留到下次启动。" }
                                ColumnLayout {
                                    objectName: "controlLayoutEditor"
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Repeater {
                                        model: root.preferences.gameControlLayout.order
                                        delegate: RowLayout {
                                            required property string modelData
                                            required property int index
                                            visible: !root.automaticMapSelection || (modelData !== "manual_map" && modelData !== "recognition")
                                            enabled: visible
                                            Layout.fillWidth: true
                                            CheckBox {
                                                objectName: "layout_enabled_" + modelData
                                                text: root.moduleNames[modelData]
                                                Layout.fillWidth: true
                                                font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                                                checked: root.preferences.gameControlLayout.enabled.indexOf(modelData) >= 0
                                                onToggled: root.setLayoutFlag("module", modelData, checked)
                                            }
                                            AppButton { objectName: "layout_up_" + modelData; text: "上移"; implicitWidth: 54; padding: 6; enabled: index > 0; Accessible.name: root.moduleNames[modelData] + "上移"; onClicked: settingsService.moveControlModule(modelData, -1) }
                                            AppButton { objectName: "layout_down_" + modelData; text: "下移"; implicitWidth: 54; padding: 6; enabled: index < root.preferences.gameControlLayout.order.length - 1; Accessible.name: root.moduleNames[modelData] + "下移"; onClicked: settingsService.moveControlModule(modelData, 1) }
                                        }
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Repeater {
                                        model: [{key: "alignedOpacity", title: "贴合不透明度控件"}, {key: "referenceOpacity", title: "定点不透明度控件"}, {key: "referencePosition", title: "定点位置编辑控件"}]
                                        delegate: CheckBox {
                                            required property var modelData
                                            objectName: "layout_display_" + modelData.key
                                            text: modelData.title
                                            enabled: root.preferences.gameControlLayout.enabled.indexOf("display") >= 0
                                            font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                                            checked: root.preferences.gameControlLayout.display[modelData.key]
                                            onToggled: root.setLayoutFlag("display", modelData.key, checked)
                                        }
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true; spacing: 8
                                    AppButton { objectName: "layout_preset_compact"; text: "精简预设"; onClicked: settingsService.setControlLayoutPreset("compact") }
                                    AppButton { objectName: "layout_preset_common"; text: "常用预设"; onClicked: settingsService.setControlLayoutPreset("common") }
                                    AppButton { objectName: "layout_save"; text: "保存布局"; primary: true; enabled: root.layoutDirty && !settingsService.busy; onClicked: settingsService.saveFields(["gameControlLayout"]) }
                                    AppButton { objectName: "layout_reset"; text: "撤销布局"; enabled: root.layoutDirty && !settingsService.busy; onClicked: settingsService.resetControlLayout() }
                                }
                            }
                        }
                        ColumnLayout {
                            objectName: "settingsDisplayColumn"
                            Layout.fillWidth: true; Layout.maximumWidth: 560
                            Layout.alignment: Qt.AlignTop
                            spacing: 16
                            Heading { text: "小抄显示"; Layout.topMargin: 0; Layout.bottomMargin: 16 }
                            RowLayout {
                                Layout.fillWidth: true; Layout.minimumHeight: 54
                                Label { text: "允许显影"; color: "#183a73"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; Layout.fillWidth: true }
                                Switch {
                                    id: visibilitySwitch
                                    objectName: "setting_sheetVisibilityEnabled"
                                    checked: root.preferences.sheetVisibilityEnabled
                                    Accessible.name: "允许显影"
                                    onToggled: settingsService.setValue("sheetVisibilityEnabled", checked)
                                    indicator: Rectangle {
                                        implicitWidth: 44; implicitHeight: 24
                                        x: visibilitySwitch.leftPadding
                                        y: visibilitySwitch.topPadding + (visibilitySwitch.availableHeight - height) / 2
                                        radius: 12
                                        color: visibilitySwitch.checked ? "#2f66eb" : "#cad8ee"
                                        border.width: visibilitySwitch.activeFocus ? 2 : 0
                                        border.color: "#1f50cc"
                                        Rectangle {
                                            width: 18; height: 18; radius: 9
                                            x: 3 + visibilitySwitch.visualPosition * (parent.width - width - 6)
                                            y: 3; color: "white"
                                        }
                                    }
                                }
                            }
                            OpacityBlock { settingKey: "sheetOpacity"; title: "不透明度" }
                            OpacityBlock { settingKey: "sheetScale"; title: "小抄大小"; minimum: 50; maximum: 150 }
                            OpacityBlock { settingKey: "referenceOpacity"; title: "定点地图不透明度" }
                            Flow {
                                Layout.fillWidth: true; spacing: 8
                                AppButton {
                                    objectName: "settingsEditReferencePosition"
                                    text: "编辑定点地图位置"; visible: !root.referenceEditing
                                    enabled: !settingsService.busy
                                    onClicked: { root.referenceEditRequested = true; clientCoordinator.beginReferenceEdit() }
                                }
                                AppButton {
                                    objectName: "settingsFinishReferencePosition"
                                    text: "完成位置编辑"; visible: root.referenceEditing
                                    enabled: !settingsService.busy; primary: true
                                    onClicked: { clientCoordinator.finishReferenceEdit(); root.referenceEditRequested = false }
                                }
                                AppButton {
                                    objectName: "settingsCancelReferencePosition"
                                    text: "取消位置编辑"; visible: root.referenceEditing || root.referenceEditRequested
                                    onClicked: { clientCoordinator.cancelReferenceEdit(); root.referenceEditRequested = false }
                                }
                            }
                            Note {
                                objectName: "settingsReferencePositionMessage"
                                visible: root.referenceEditing || root.referenceEditRequested
                                text: visible && typeof clientCoordinator !== "undefined" ? clientCoordinator.message : ""
                            }
                            OpacityBlock { settingKey: "interfaceOpacity"; title: "界面不透明度"; minimum: 65; maximum: 95 }
                            Disclosure { objectName: "advancedDisplayToggle"; text: root.advancedDisplay ? "收起更多显示设置" : "更多显示设置"; expanded: root.advancedDisplay; onClicked: root.advancedDisplay = !root.advancedDisplay }
                            ColumnLayout {
                                visible: root.advancedDisplay
                                Layout.fillWidth: true; spacing: 10
                                Repeater {
                                    model: [
                                        {key: "referenceSheetVisible", title: "显示定点地图"},
                                        {key: "qtControlsVisible", title: "显示游戏控件"},
                                        {key: "reduceMotion", title: "减弱动态效果"},
                                        {key: "uiSoundEnabled", title: "界面提示音"}
                                    ]
                                    delegate: CheckBox {
                                        required property var modelData
                                        objectName: "setting_" + modelData.key
                                        text: modelData.title
                                        font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                                        checked: root.preferences[modelData.key]
                                        onToggled: {
                                            if (modelData.key === "qtControlsVisible") {
                                                if (checked) clientCoordinator.showControls()
                                                else clientCoordinator.hideControls()
                                            } else settingsService.setValue(modelData.key, checked)
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#edf2fa" }
                    ClientChestAutoTap { Layout.fillWidth: true }
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#edf2fa" }
                    ClientFloorRegion {
                        Layout.fillWidth: true
                        visible: !root.automaticMapSelection
                        enabled: visible
                    }
                    Disclosure { objectName: "modeSettingsToggle"; text: root.modeExpanded ? "收起模式与路线" : "模式与路线"; expanded: root.modeExpanded; onClicked: root.modeExpanded = !root.modeExpanded }
                    ColumnLayout {
                        visible: root.modeExpanded
                        Layout.fillWidth: true; Layout.maximumWidth: 640
                        spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    SeasonSelector {
                        id: settingsContextSelector; objectName: "setting_gameContext"
                        seasons: settingsService.seasonOptions
                        season: root.draftSeason; mode: root.preferences.gameMode
                        enabled: !settingsService.busy
                        onContextRequested: function(season, mode) { settingsService.setValue("gameSeason", season); settingsService.setValue("gameMode", mode) }
                    }
                    Item { Layout.fillWidth: true }
                }
                Note {
                    objectName: "settingsS1FloorObservation"; visible: root.automaticMapSelection
                    text: "自动识别地图与楼层（B1～B4）"
                }
                RowLayout {
                    objectName: "settingsMapSelectionActions"
                    visible: !root.automaticMapSelection
                    enabled: !root.automaticMapSelection
                    Layout.fillWidth: true
                    Label { text: "选图方式"; color: "#354f75"; font.pixelSize: 13; Layout.fillWidth: true }
                    AppButton {
                        objectName: "setting_mapSelectionMode_auto"
                        text: "自动识别"; selected: root.preferences.mapSelectionMode !== "manual"
                        onClicked: root.saveMapSelectionMode("auto")
                    }
                    AppButton {
                        objectName: "setting_mapSelectionMode_manual"
                        text: "手动选图"; selected: root.preferences.mapSelectionMode === "manual"
                        onClicked: root.saveMapSelectionMode("manual")
                    }
                }
                Note {
                    objectName: "settingsMapSelectionExplanation"
                    visible: !root.automaticMapSelection && root.preferences.mapSelectionMode === "manual"
                    text: root.automaticMapSelection
                        ? "" : "手动选图：用快捷键或游戏控件选择地图。"
                }
                GridLayout {
                    columns: root.width < 560 ? 1 : 2
                    columnSpacing: 16; rowSpacing: 8
                    Layout.fillWidth: true
                    Label { text: "默认路线作者"; color: "#354f75"; font.pixelSize: 13 }
                    ComboBox {
                        id: routeProfile
                        objectName: "setting_routeProfileId"
                        Layout.fillWidth: true
                        model: root.profileOptions; textRole: "name"; valueRole: "id"
                        enabled: count > 0 && root.profilesCurrent && !settingsService.busy
                        currentIndex: { for(var i=0;i<root.profileOptions.length;i++) if(root.profileOptions[i].id===root.draftProfile)return i; return -1 }
                        displayText: !root.profilesCurrent ? "正在读取对应赛季作者…" : !settingsContextSelector.registeredContext(root.draftSeason, root.preferences.gameMode) && count === 0 ? settingsContextSelector.seasonLabel(root.draftSeason) + " 资源待补齐" : currentIndex < 0 ? "当前作者未安装" : currentText
                        Accessible.name: "默认路线作者"
                        onActivated: {
                            if (!root.profilesCurrent || !currentValue) return
                            var profiles = JSON.parse(JSON.stringify(root.preferences.contextRouteProfiles || {}))
                            profiles[root.draftContextKey] = currentValue
                            settingsService.setValue("contextRouteProfiles", profiles)
                            if (root.draftSeason === "s0") {
                                var legacy = JSON.parse(JSON.stringify(root.preferences.modeRouteProfiles || {}))
                                legacy[root.preferences.gameMode] = currentValue
                                settingsService.setValue("modeRouteProfiles", legacy)
                                settingsService.setValue("routeProfileId", currentValue)
                            }
                        }
                    }
                    Label { text: "全棺路线版本"; color: "#354f75"; font.pixelSize: 13 }
                    ComboBox {
                        objectName: "setting_zhan10CoffinVariantId"
                        Layout.fillWidth: true
                        model: settingsService.coffinVariantOptions; textRole: "name"; valueRole: "id"
                        currentIndex: { for (var i=0;i<settingsService.coffinVariantOptions.length;i++) if(settingsService.coffinVariantOptions[i].id===root.preferences.zhan10CoffinVariantId) return i; return -1 }
                        displayText: currentIndex < 0 ? "当前版本不可用（偏好保留）" : currentText
                        enabled: !settingsService.busy && root.preferences.gameMode===settingsService.coffinVariantContext.mode
                                 && root.draftSeason === (settingsService.coffinVariantContext.season || "s0")
                                 && root.draftProfile===settingsService.coffinVariantContext.profileId
                                 && settingsService.coffinVariantOptions.some(function(row) { return row.available })
                        delegate: ItemDelegate {
                            required property var modelData
                            width: parent ? parent.width : implicitWidth
                            text: modelData.name + (modelData.available ? "" : " · " + modelData.reason)
                            enabled: modelData.available
                        }
                        Accessible.name: "全棺路线版本"
                        onActivated: settingsService.selectCoffinVariant(currentValue)
                    }
                    Note { text: settingsService.coffinVariantMessage; Layout.columnSpan: parent.columns }
                }
                    }

                }
            }
        }
        Note { objectName: "settingsMessage"; text: settingsService.message; Accessible.role: Accessible.StaticText }
        Flow {
            Layout.fillWidth: true; spacing: 8
            AppButton { objectName: "settingsSave"; text: settingsService.busy ? "正在保存…" : "保存更改"; primary: true; enabled: settingsService.dirty && !settingsService.busy && !settingsService.shortcutRecording; onClicked: { forceActiveFocus(); settingsService.save() } }
            AppButton { objectName: "settingsReload"; text: "重新读取"; enabled: !settingsService.busy; onClicked: settingsService.reload() }
            AppButton { objectName: "settingsReset"; text: "撤销草稿"; enabled: settingsService.dirty && !settingsService.busy; onClicked: settingsService.resetDraft() }
            AppButton { objectName: "settingsReplayOnboarding"; text: "重新查看快捷键引导"; enabled: !settingsService.busy; onClicked: onboardingService.open() }
        }
    }
}
