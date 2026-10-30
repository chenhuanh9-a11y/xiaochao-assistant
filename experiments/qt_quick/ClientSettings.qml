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
    readonly property var actionNames: ["显示/隐藏面板", "上一个候选", "下一个候选", "确认本局", "切换楼层", "结束本局", "显示/隐藏小抄", "开始本局", "开始/结束本局"]

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
        font.pixelSize: 12
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

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "客户端设置"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 26; font.bold: true }
            Item { Layout.fillWidth: true }
            Label { text: settingsService.busy ? "正在保存…" : settingsService.dirty ? "未保存" : "已同步"; color: "#526785"; font.pixelSize: 12 }
        }
        Note { text: "编辑隔离客户端的偏好，保存后保留到下次启动。" }
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth - 18
                spacing: 10
                Heading { text: "显示"; Layout.topMargin: 2 }
                Repeater {
                    model: [
                        {key: "sheetOpacity", title: "贴合小抄不透明度", min: 0, max: 100, scale: 100},
                        {key: "referenceOpacity", title: "定点地图不透明度", min: 0, max: 100, scale: 100},
                        {key: "interfaceOpacity", title: "界面不透明度", min: 65, max: 95, scale: 100},
                        {key: "sheetScale", title: "小抄大小", min: 50, max: 150, scale: 100}
                    ]
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label { text: modelData.title; color: "#354f75"; font.pixelSize: 13; Layout.preferredWidth: 164 }
                        AppSlider {
                            objectName: "setting_" + modelData.key
                            Layout.fillWidth: true
                            Layout.minimumWidth: 80
                            from: modelData.min; to: modelData.max; stepSize: 1
                            value: Math.round(root.preferences[modelData.key] * modelData.scale)
                            Accessible.name: modelData.title
                            onMoved: settingsService.setValue(modelData.key, value / modelData.scale)
                        }
                        Label { text: Math.round(root.preferences[modelData.key] * modelData.scale) + "%"; color: "#354f75"; font.pixelSize: 13; Layout.preferredWidth: 44; horizontalAlignment: Text.AlignRight }
                    }
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    Repeater {
                        model: [
                            {key: "sheetVisibilityEnabled", title: "显示贴合小抄"},
                            {key: "referenceSheetVisible", title: "显示定点地图"},
                            {key: "orbVisible", title: "显示悬浮球"},
                            {key: "reduceMotion", title: "减弱动态效果"},
                            {key: "uiSoundEnabled", title: "界面提示音"},
                            {key: "difficultGridEnabled", title: "困难模式网格"}
                        ]
                        delegate: CheckBox {
                            required property var modelData
                            objectName: "setting_" + modelData.key
                            text: modelData.title
                            enabled: modelData.key !== "uiSoundEnabled" && modelData.key !== "difficultGridEnabled"
                            font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                            checked: root.preferences[modelData.key]
                            onToggled: settingsService.setValue(modelData.key, checked)
                        }
                    }
                }
                Note { text: "界面提示音、困难模式网格尚未接通；现有偏好会保留。" }
                Heading { text: "模式与路线" }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton { text: "噩梦"; selected: root.preferences.gameMode === "nightmare"; onClicked: settingsService.setValue("gameMode", "nightmare") }
                    AppButton { text: "困难"; selected: root.preferences.gameMode === "difficult"; onClicked: settingsService.setValue("gameMode", "difficult") }
                    Item { Layout.fillWidth: true }
                }
                Note { text: "保存默认工作模式与路线作者；可用作者来自当前模式的本地图包。" }
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
                        enabled: count > 0 && root.profilesMode===root.preferences.gameMode && !settingsService.busy
                        currentIndex: { for(var i=0;i<root.profileOptions.length;i++) if(root.profileOptions[i].id===root.preferences.routeProfileId)return i; return -1 }
                        displayText: currentIndex < 0 ? "当前作者未安装" : currentText
                        Accessible.name: "默认路线作者"
                        onActivated: {
                            var profiles = JSON.parse(JSON.stringify(root.preferences.modeRouteProfiles || {}))
                            profiles[root.preferences.gameMode] = currentValue
                            settingsService.setValue("modeRouteProfiles", profiles)
                            settingsService.setValue("routeProfileId", currentValue)
                        }
                    }
                    Label { text: "全棺路线变体 ID"; color: "#354f75"; font.pixelSize: 13 }
                    Field {
                        Layout.fillWidth: true
                        text: root.preferences.zhan10CoffinVariantId
                        enabled: false
                        Accessible.name: "全棺路线变体 ID"
                        onEditingFinished: { settingsService.setValue("zhan10CoffinVariantId", text); text = Qt.binding(function() { return root.preferences.zhan10CoffinVariantId }) }
                    }
                }
                Heading { text: "快捷键" }
                Note { text: "仅保存快捷键偏好；此客户端尚未连接全局快捷键。支持 Ctrl、Alt、Shift 组合；清空可解绑，重复组合会提示错误。" }
                GridLayout {
                    columns: root.width < 560 ? 1 : 2
                    Layout.fillWidth: true
                    columnSpacing: 16; rowSpacing: 8
                    Label { text: "地图键"; color: "#354f75"; font.pixelSize: 13 }
                    Field {
                        objectName: "setting_hotkey"
                        Layout.fillWidth: true
                        text: root.preferences.hotkey
                        placeholderText: "例如 Ctrl+G"
                        Accessible.name: "地图键"
                        onEditingFinished: { settingsService.setValue("hotkey", text); text = Qt.binding(function() { return root.preferences.hotkey }) }
                    }
                    Repeater {
                        model: root.actionNames
                        delegate: RowLayout {
                            required property string modelData
                            required property int index
                            Layout.columnSpan: root.width < 560 ? 1 : 2
                            Layout.fillWidth: true
                            Label { text: modelData; color: "#354f75"; font.pixelSize: 13; Layout.preferredWidth: 164 }
                            Field {
                                objectName: "setting_action_" + index
                                Layout.fillWidth: true
                                text: root.preferences.actionHotkeys[index]
                                placeholderText: "未绑定"
                                Accessible.name: modelData + "快捷键"
                                onEditingFinished: { root.setAction(index, text); text = Qt.binding(function() { return root.preferences.actionHotkeys[index] }) }
                            }
                        }
                    }
                }
                Heading { text: "悬浮面板控件" }
                Note { text: "选择面板上显示的模块。当前模块顺序会保留。" }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    Repeater {
                        model: [{key: "display", title: "显示"}, {key: "map", title: "模式与选图"}, {key: "recognition", title: "候选与再识别"}, {key: "round", title: "本局动作"}]
                        delegate: CheckBox {
                            required property var modelData
                            text: modelData.title
                            font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                            checked: root.preferences.gameControlLayout.enabled.indexOf(modelData.key) >= 0
                            onToggled: root.setLayoutFlag("module", modelData.key, checked)
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
                            text: modelData.title
                            font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                            checked: root.preferences.gameControlLayout.display[modelData.key]
                            onToggled: root.setLayoutFlag("display", modelData.key, checked)
                        }
                    }
                }
                Item { Layout.preferredHeight: 10 }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#d6e0ef" }
        Note { objectName: "settingsMessage"; text: settingsService.message; Accessible.role: Accessible.StaticText }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            AppButton { objectName: "settingsSave"; text: settingsService.busy ? "正在保存…" : "保存更改"; primary: true; enabled: settingsService.dirty && !settingsService.busy; onClicked: { forceActiveFocus(); settingsService.save() } }
            AppButton { objectName: "settingsReload"; text: "重新读取"; enabled: !settingsService.busy; onClicked: settingsService.reload() }
            AppButton { objectName: "settingsReset"; text: "撤销草稿"; enabled: settingsService.dirty && !settingsService.busy; onClicked: settingsService.resetDraft() }
        }
    }
}
