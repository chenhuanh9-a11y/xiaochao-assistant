pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "clientManualSelection"
    property var service: typeof manualSelection !== "undefined" ? manualSelection : null
    readonly property var state: service ? service.snapshot : ({})
    implicitWidth: 700
    implicitHeight: 590
    visible: !!state.visible
    color: "#f3f6fc"
    radius: 14
    border.color: "#d9e4f4"
    border.width: 1

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "选择本局地图"; color: "#142b4b"; font.pixelSize: 22; font.bold: true }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "manualPickerClose"
                text: "关闭" + (root.state.keyLabels && root.state.keyLabels["manual-picker-close"] ? "  " + root.state.keyLabels["manual-picker-close"] : "")
                onClicked: if (root.service) root.service.close()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "选图方式"; color: "#526785" }
            Button {
                objectName: "manualModeAuto"; text: "自动识别"; checkable: true
                checked: root.state.mode === "auto"; enabled: !!root.state.canSetMode
                onClicked: if (root.service) root.service.setMode("auto")
            }
            Button {
                objectName: "manualModeManual"; text: "手动选图"; checkable: true
                checked: root.state.mode === "manual"; enabled: !!root.state.canSetMode
                onClicked: if (root.service) root.service.setMode("manual")
            }
            Item { Layout.fillWidth: true }
            Label { text: root.state.gameMode === "difficult" ? "困难" : "噩梦"; color: "#526785" }
        }
        Label {
            Layout.fillWidth: true
            text: root.state.mode === "manual" ? "选择地图后，楼层识别和地图贴合继续自动进行。" : "自动识别已开启，也可直接指定本局地图。"
            color: "#526785"; wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                objectName: "manualPickerBack"; text: "‹ 路线分组"
                visible: !!root.state.canBack
                onClicked: if (root.service) root.service.back()
            }
            Label {
                objectName: "manualPickerHeading"
                text: root.state.stage === "groups" ? "选择路线分组" : (root.state.groupName || "地图")
                color: "#142b4b"; font.pixelSize: 16; font.bold: true
            }
            Item { Layout.fillWidth: true }
        }
        GridLayout {
            columns: 2
            Layout.fillWidth: true
            Layout.fillHeight: true
            rowSpacing: 10
            columnSpacing: 10
            Repeater {
                model: 4
                delegate: Button {
                    id: card
                    required property int index
                    readonly property var option: (root.state.options || [])[index] || ({})
                    readonly property bool present: !!option.id
                    property int pressRevision: -1
                    objectName: "manualPickerOption_" + index
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 120
                    enabled: present && !!root.state.canChoose
                    opacity: present ? 1 : 0
                    focusPolicy: present ? Qt.StrongFocus : Qt.NoFocus
                    onPressed: pressRevision = root.state.revision
                    onClicked: if (root.service) root.service.choose(index, pressRevision)
                    background: Rectangle {
                        radius: 9
                        color: card.down ? "#dce8ff" : card.hovered ? "#edf3ff" : "#ffffff"
                        border.width: card.activeFocus ? 2 : 1
                        border.color: card.activeFocus ? "#2f66eb" : "#d4dfed"
                    }
                    contentItem: ColumnLayout {
                        spacing: 6
                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            visible: card.option.kind === "map"
                            Image {
                                id: thumbnail
                                anchors.fill: parent
                                source: root.visible ? (card.option.thumbnailUrl || "") : ""
                                sourceSize.width: 320
                                sourceSize.height: 190
                                asynchronous: true
                                cache: false
                                fillMode: Image.PreserveAspectFit
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: !thumbnail.source.toString() || thumbnail.status === Image.Error
                                text: "暂无预览 · 可按名称选择"
                                color: "#697c96"; font.pixelSize: 12
                            }
                        }
                        Item { Layout.fillHeight: true; visible: card.option.kind === "group" }
                        Label {
                            Layout.fillWidth: true
                            text: card.option.name || ""
                            color: "#142b4b"; font.pixelSize: 15; font.bold: true
                            wrapMode: Text.WordWrap
                            horizontalAlignment: Text.AlignHCenter
                            maximumLineCount: 2; elide: Text.ElideRight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: card.option.kind === "group" ? String(card.option.count || 0) + " 张地图" : (card.option.shortcut || "")
                            visible: card.option.kind === "group" || !!card.option.shortcut
                            color: "#526785"; horizontalAlignment: Text.AlignHCenter
                        }
                        Item { Layout.fillHeight: true; visible: card.option.kind === "group" }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                objectName: "manualPickerPrevious"; text: "‹ 上一页"
                enabled: (root.state.page || 0) > 0
                onClicked: if (root.service) root.service.turn(-1)
            }
            Item { Layout.fillWidth: true }
            Label {
                objectName: "manualPickerPage"
                text: String((root.state.page || 0) + 1) + " / " + String(root.state.pageCount || 1)
                color: "#526785"
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "manualPickerNext"; text: "下一页 ›"
                enabled: (root.state.page || 0) + 1 < (root.state.pageCount || 1)
                onClicked: if (root.service) root.service.turn(1)
            }
        }
        Label {
            Layout.fillWidth: true
            text: root.state.reason || root.state.message || "先选路线分组，再选择地图。"
            color: root.state.reason ? "#a25822" : "#526785"
            wrapMode: Text.WordWrap
            font.pixelSize: 12
        }
    }
}
