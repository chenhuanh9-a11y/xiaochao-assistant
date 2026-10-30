pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    objectName: "clientRecognition"
    readonly property var state: session.snapshot
    readonly property var candidates: state.candidateRows || []
    readonly property var floors: state.availableFloors || []
    readonly property int selectedIndex: typeof state.selectedCandidateIndex === "number" ? state.selectedCandidateIndex : -1
    readonly property bool hasSelection: selectedIndex >= 0 && selectedIndex < candidates.length
    function available(action) {
        return !!state.commands && !!state.commands[action] && state.commands[action].available
               && !session.busy && !state.resourceLocked
    }
    function floorName(floor) { return String(floor) === "B1" ? "B1" : "F" + floor }

    ColumnLayout {
        anchors.fill: parent
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Label { text: "地图识别"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 26; font.bold: true }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionCapture"; text: "重新识别"; enabled: page.available("capture_now"); onClicked: session.dispatch("capture_now") }
        }
        Label {
            Layout.fillWidth: true
            text: page.state.identityLocked === true ? "本局地图已确认。重新选图可解除当前地图确认。" : "核对识别候选，再确认本局地图。"
            color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AppButton { objectName: "recognitionPrevious"; text: "上一候选"; enabled: page.candidates.length > 0 && page.available("previous_candidate"); onClicked: session.dispatch("previous_candidate") }
            AppButton { objectName: "recognitionNext"; text: "下一候选"; enabled: page.candidates.length > 0 && page.available("next_candidate"); onClicked: session.dispatch("next_candidate") }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionReselect"; text: "重新选图"; enabled: page.available("reselect_map"); onClicked: session.dispatch("reselect_map") }
            AppButton { objectName: "recognitionConfirm"; text: page.state.identityLocked === true ? "地图已确认" : "确认此地图"; primary: true; enabled: page.hasSelection && page.available("confirm_route"); onClicked: session.dispatch("confirm_route") }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150
            color: "white"; radius: 12; border.color: "#d6e0ef"
            ListView {
                id: candidateList
                objectName: "recognitionCandidateList"
                anchors.fill: parent; anchors.margins: 8
                clip: true; spacing: 4
                model: page.candidates
                currentIndex: page.selectedIndex
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    id: candidate
                    required property var modelData
                    required property int index
                    objectName: "recognitionCandidate_" + index
                    width: candidateList.width
                    height: detail.text.length > 0 ? 76 : 56
                    padding: 12
                    enabled: page.available("select_candidate")
                    highlighted: index === page.selectedIndex
                    Accessible.name: (modelData.name || modelData.mapId || "未命名地图") + " " + (modelData.floor ? page.floorName(modelData.floor) : "")
                    onClicked: session.dispatchWithPayload("select_candidate", {index: index})
                    background: Rectangle {
                        radius: 8
                        color: candidate.highlighted ? "#e7effc" : candidate.hovered ? "#f3f6fc" : "white"
                        border.color: candidate.highlighted || candidate.activeFocus ? "#2f66eb" : "transparent"
                        border.width: candidate.activeFocus ? 2 : 1
                    }
                    contentItem: ColumnLayout {
                        spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: candidate.modelData.name || candidate.modelData.mapId || "未命名地图"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 14; font.bold: candidate.highlighted; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label { text: candidate.modelData.floor ? page.floorName(candidate.modelData.floor) : "楼层待核对"; color: "#526785"; font.pixelSize: 12 }
                            Label { text: candidate.highlighted ? "当前候选" : ""; color: "#1f50cc"; font.pixelSize: 12 }
                        }
                        Label { id: detail; text: candidate.modelData.reason || ""; visible: text.length > 0; Layout.fillWidth: true; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; maximumLineCount: 2; wrapMode: Text.WordWrap; elide: Text.ElideRight }
                    }
                }
            }
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(380, parent.width - 40)
                visible: page.candidates.length === 0
                spacing: 10
                Label { text: session.busy ? "正在读取识别候选…" : "尚无识别候选"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 18; font.bold: true; Layout.alignment: Qt.AlignHCenter }
                Label { text: page.available("capture_now") ? "打开游戏地图后，点击“重新识别”获取候选。" : "当前识别入口不可用，候选与确认操作尚未开放。"; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label { text: "目标楼层"; color: "#354f75"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13 }
            Repeater {
                model: page.floors
                delegate: AppButton {
                    required property string modelData
                    objectName: "recognitionFloor_" + modelData
                    text: page.floorName(modelData)
                    implicitWidth: 58
                    selected: String(page.state.floor) === modelData
                    enabled: page.available("set_floor")
                    onClicked: session.dispatchWithPayload("set_floor", {floor: modelData})
                }
            }
            Label { visible: page.floors.length === 0; text: "楼层选项尚不可用"; color: "#526785"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionCycleFloor"; text: "切换楼层"; enabled: page.floors.length > 0 && page.available("cycle_floor"); onClicked: session.dispatch("cycle_floor") }
        }
        Label { text: "楼层选择是显示目标，地图确认不代表当前画面已经贴合。"; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        Label { objectName: "recognitionMessage"; text: session.message; color: "#354f75"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    }
}
