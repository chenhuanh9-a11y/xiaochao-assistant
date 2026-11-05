pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    objectName: "clientRecognition"
    readonly property var state: session.snapshot
    readonly property var privateExperiment: state.privateExperiment || ({})
    readonly property var candidates: state.candidateRows || []
    readonly property var floors: state.availableFloors || []
    readonly property int selectedIndex: typeof state.selectedCandidateIndex === "number" ? state.selectedCandidateIndex : -1
    readonly property bool hasSelection: selectedIndex >= 0 && selectedIndex < candidates.length
    property var groupChoices: []
    property string groupChoicesKey: ""
    readonly property bool groupVisible: !!state.groupId && groupChoices.length > 1
        && (state.identityPhase === "GROUP_LOCKED" || state.identityPhase === "MEMBER_VERIFIED"
            || state.identityPhase === "MANUAL_MEMBER_CONFIRMED")
        && state.candidateEditMode !== true
    readonly property string groupMember: state.currentGroupMemberId || state.provisionalMapId
        || state.durableMapId || state.mapId || ""
    readonly property string groupDesired: state.desiredGroupMemberId || ""
    readonly property string groupChoice: groupDesired || groupMember
    readonly property string groupChoiceStatus: state.groupMemberChoiceStatus || ""
    readonly property bool groupAwaiting: groupChoiceStatus === "awaiting-geometry" || groupChoiceStatus === "awaiting-material"
    readonly property bool groupHeld: state.manualGroupMemberHeld === true
    readonly property bool groupPending: state.groupMemberSelectionPending === true
    Component.onCompleted: syncGroupChoices()
    onStateChanged: syncGroupChoices()
    function syncGroupChoices() {
        const ids = state.groupMemberIds || []
        const named = state.groupMemberRows || []
        const rows = []
        for (let i = 0; i < ids.length; ++i) {
            const id = String(ids[i])
            if (!id || rows.some(row => row.mapId === id)) continue
            const name = named.find(row => row.mapId === id)
                || candidates.find(row => row.mapId === id)
            rows.push({mapId: id, name: name && name.name ? name.name : id})
        }
        const key = JSON.stringify(rows)
        if (key !== groupChoicesKey) { groupChoicesKey = key; groupChoices = rows }
    }
    function chooseCandidate(index, row) {
        let payload = {index: index}
        if (row && row.selectionSeason === "s1")
            payload.candidateMapId = row.mapId
        if (row && row.structureSupported === true) {
            payload.mapId = row.mapId
            payload.structureFrameId = row.structureFrameId
            payload.structureGeneration = row.structureGeneration
            payload.structureRoundId = row.structureRoundId
        }
        session.dispatchWithPayload("select_candidate", payload)
    }
    function groupName(id) {
        const row = groupChoices.find(row => row.mapId === id)
        return row ? row.name : (id || "待核对")
    }
    function groupSelectionText() {
        if (groupDesired && groupAwaiting)
            return "已选" + groupName(groupDesired) + (groupChoiceStatus === "awaiting-material" ? "，正在切换；当前地图" : "，等待定位；当前地图") + groupName(groupMember)
        if (groupDesired || groupHeld) return "本局已手动选择：" + groupName(groupChoice)
        return (state.identityPhase === "MEMBER_VERIFIED" ? "当前地图：" : "当前暂用：") + groupName(groupMember) + " · 请选择本局地图"
    }
    function groupReason(action) {
        if (state.contextPending || state.roundPreparing) return "正在切换环境，请稍后选择"
        if (state.resourceLocked) return "正在更新资源，请稍后选择"
        if (groupPending) return "正在核对所选地图，当前显示暂不改变"
        if (session.busy && state.pendingAction !== "refresh_map") return "当前操作尚未完成，请稍后选择"
        if (!state.roundActive) return "请先开始本局识别"
        if (!state.commands || !state.commands[action] || !state.commands[action].available)
            return state.groupMemberSelectionReason || "当前画面尚不支持组内切换，请打开游戏地图后再试"
        return ""
    }
    function groupPayload(id) {
        return {mapId: id, groupId: state.groupId, groupGeneration: state.groupGeneration,
            cycleId: state.cycleId, floor: String(state.floor || "")}
    }
    function available(action) {
        return !!state.commands && !!state.commands[action] && state.commands[action].available
               && !session.busy && !state.resourceLocked
    }
    function floorName(floor) { return state.season === "s1" ? "B" + Math.abs(Number(floor)) : String(floor) === "B1" ? "B1" : "F" + floor }

    ColumnLayout {
        anchors.fill: parent
        spacing: 16
        RowLayout {
            visible: page.state.season === "s1"
            Layout.fillWidth: true
            AppButton {
                objectName: "recognitionPrepareManualAlignment"
                text: "准备手动贴合"; visible: !page.state.manualAlignmentSession
                enabled: page.available("prepare_manual_alignment")
                onClicked: session.dispatch("prepare_manual_alignment")
            }
            AppButton {
                text: "选择本局地图"; enabled: page.state.manualMapPickAvailable === true
                onClicked: clientCoordinator.requestManualMapSelection()
            }
            AppButton {
                text: "贴合当前画面"; enabled: page.available("fit_manual_frame")
                onClicked: clientCoordinator.fitManualFromMain()
            }
            Label {
                text: page.state.structureScanInProgress === true
                    ? "自动候选：首个候选可先显示，后台继续扫描；扫描结束后可切换，当前仍未锁定地图。"
                    : page.state.candidateProvisionalDisplayEnabled === true
                    ? "自动候选：每次开图重新筛选，可信位姿自动显示候选整图；可直接切换，不代表地图身份已锁定。"
                    : page.state.candidateStructureAvailable === true && !page.state.manualAlignmentSession
                    ? "当前画面可读取静态核验过的 B1–B4 并列出结构候选；请选择并确认本局地图。"
                    : "人工选择地图；自动观察当前 B1～B4。B3/B4 已有静态截图核验，实时贴合待测，识别不确定时停止贴合。默认目标不代表已观察。"
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "地图识别"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 26; font.bold: true }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionCapture"; text: "回游戏并重新识别"; enabled: page.available("capture_now"); onClicked: clientCoordinator.recaptureFromMain() }
        }
        Label {
            Layout.fillWidth: true
            visible: page.state.candidateProvisionalDisplayEnabled === true
            text: page.state.evidenceKind === "recorded"
                ? "输入来源：录制画面，无实机显示授权。"
                : "自动候选当前支持 B1/B2；B3/B4 可用手动选图与四层自动读层。整图包含尚未探索的区域。"
            color: "#526785"; wrapMode: Text.WordWrap
        }
        Label {
            objectName: "recognitionPrivateExperiment"
            visible: page.privateExperiment.privateExperimental === true
            text: page.privateExperiment.privateExperimentalLocked === true
                ? "实验锁定（未正式校准） · " + (page.privateExperiment.privateLockMapId || "")
                : "实验识别中 · 候选 " + (page.privateExperiment.privatePoolMapIds || []).length + " 个 · 尚未锁定"
            color: "#946000"; wrapMode: Text.WordWrap; Layout.fillWidth: true
        }
        Label {
            Layout.fillWidth: true
            text: page.state.candidateProvisionalDisplayEnabled === true ? "显示的是未锁定候选整图，包含未知区域；无需确认才能显示。" : page.groupVisible ? "相似组内可手动切换地图；手动选择不代表已通过当前楼层的几何核对。"
                : page.state.identityLocked === true ? "本局地图已确认。重新选图可解除当前地图确认。" : "核对识别候选，再确认本局地图。"
            color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AppButton { objectName: "recognitionPrevious"; visible: !page.groupVisible; text: "上一候选"; enabled: page.candidates.length > 0 && page.available("previous_candidate"); onClicked: session.dispatch("previous_candidate") }
            AppButton { objectName: "recognitionNext"; visible: !page.groupVisible; text: "下一候选"; enabled: page.candidates.length > 0 && page.available("next_candidate"); onClicked: session.dispatch("next_candidate") }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionReselect"; text: "重新选图"; enabled: page.available("reselect_map"); onClicked: session.dispatch("reselect_map") }
            AppButton { objectName: "recognitionConfirm"; visible: !page.groupVisible; text: page.state.candidateProvisionalDisplayEnabled === true ? "人工确认本局地图（可选）" : page.state.identityLocked === true ? "地图已确认" : "确认此地图"; primary: true; enabled: page.hasSelection && page.available("confirm_route"); onClicked: session.dispatch("confirm_route") }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150
            color: "white"; radius: 16; border.width: 1; border.color: "#e3e9f3"
            ListView {
                id: candidateList
                objectName: "recognitionCandidateList"
                visible: !page.groupVisible
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
                    onClicked: page.chooseCandidate(index, modelData)
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
                visible: !page.groupVisible && page.candidates.length === 0
                spacing: 10
                Label { text: session.busy ? "正在读取识别候选…" : "尚无识别候选"; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 18; font.bold: true; Layout.alignment: Qt.AlignHCenter }
                Label { text: page.available("capture_now") ? "先打开游戏地图，再点击“回游戏并重新识别”；也可使用游戏内控制面板的“重新识别”。" : "当前识别入口不可用，候选与确认操作尚未开放。"; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
            }
            ScrollView {
                anchors.fill: parent; anchors.margins: 16; visible: page.groupVisible
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: parent.width; spacing: 12
                    Label { text: "相似地图切换"; color: "#142b4b"; font.pixelSize: 18; font.bold: true }
                    Label {
                        objectName: "groupMemberCurrent"
                        text: page.groupSelectionText()
                        color: "#17365e"; font.pixelSize: 14; font.bold: true
                        wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        Repeater {
                            model: page.groupChoices
                            delegate: AppButton {
                                required property var modelData
                                objectName: "groupMember_" + modelData.mapId
                                text: modelData.name + (selected ? (page.groupDesired || page.groupHeld ? " · 本局选择" : " · 暂用") : "")
                                selected: modelData.mapId === page.groupChoice
                                enabled: !(selected && (page.groupDesired || page.groupHeld)) && page.groupReason("select_group_member") === ""
                                onClicked: session.dispatchWithPayload("select_group_member", page.groupPayload(modelData.mapId))
                            }
                        }
                    }
                    Label {
                        objectName: "groupMemberReason"
                        text: page.groupReason("select_group_member") || (page.groupAwaiting ? "将自动重试，也可选择本组其他地图。" : page.state.groupMemberSelectionReason || "选择需通过当前帧核对；缺少作者图时沿用现有底图规则。")
                        color: "#526785"; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Label { visible: page.groupHeld; text: "手动选择在本局当前组内保持；切换楼层后仍需核对该层。结束本局或重新选图会清除。"; color: "#526785"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label { text: "目标楼层"; color: "#354f75"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13 }
            AppButton {
                objectName: "recognitionFloor_auto"
                visible: page.state.season === "s1" && page.state.automaticFloorObservationAvailable === true
                text: "自动 B1～B4"; selected: !page.state.manualFloor
                enabled: page.available("set_floor")
                onClicked: session.dispatchWithPayload("set_floor", {floor: "auto"})
            }
            Repeater {
                model: page.floors
                delegate: AppButton {
                    required property string modelData
                    objectName: "recognitionFloor_" + modelData
                    visible: !(page.state.season === "s1" && page.state.automaticFloorObservationAvailable === true)
                    text: page.floorName(modelData) + (page.state.season === "s1" ? " 回退" + ((modelData === "-3" || modelData === "-4") ? "（实时贴合待测）" : "") : "")
                    implicitWidth: page.state.season === "s1" ? Math.max(58, contentItem.implicitWidth + leftPadding + rightPadding) : 58
                    selected: page.state.season === "s1" ? String(page.state.manualFloor || "") === modelData : String(page.state.floor) === modelData
                    enabled: page.available("set_floor")
                    onClicked: session.dispatchWithPayload("set_floor", {floor: modelData})
                }
            }
            Label { visible: page.floors.length === 0; text: "楼层选项尚不可用"; color: "#526785"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "recognitionCycleFloor"; visible: !(page.state.season === "s1" && page.state.automaticFloorObservationAvailable === true); text: "切换楼层"; enabled: page.floors.length > 0 && page.available("cycle_floor"); onClicked: session.dispatch("cycle_floor") }
        }
        Label { text: page.state.season === "s1" && page.state.automaticFloorObservationAvailable === true ? "楼层随当前游戏画面自动同步；未知时停止贴合。地图确认不代表当前画面已经贴合。" : "楼层选择是显示目标，地图确认不代表当前画面已经贴合。"; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        Label {
            objectName: "recognitionCurrentFloor"
            visible: page.state.season === "s1"
            text: (page.state.floorSource === "automatic-current-frame" || (page.state.candidateStructureAvailable === true && page.state.mapOpen === true)) && page.state.observedFloor ? "自动观察当前 " + page.floorName(page.state.observedFloor) + ((page.state.observedFloor === "-3" || page.state.observedFloor === "-4") ? "（实时贴合待测）" : "")
                : page.state.floorSource === "manual-unvalidated-fallback" ? "当前楼层未知 · 人工回退 " + page.floorName(page.state.targetFloor)
                : "当前楼层未知" + (page.state.targetFloor ? " · 目标 " + page.floorName(page.state.targetFloor) : "")
            color: "#526785"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
        }
        Label { objectName: "recognitionMessage"; text: session.message; color: "#354f75"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    }
}
