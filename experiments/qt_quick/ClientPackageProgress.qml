import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: card
    property var localService: null
    property var managementService: null
    property var onlineState: ({})
    readonly property bool managementActive: !!managementService && managementService.busy
    readonly property var activeLocalService: localService && localService.busy ? localService : managementActive ? managementService : null
    readonly property bool localActive: !!activeLocalService || (!localService && !!onlineState.localBusy)
    readonly property bool onlineActive: !!onlineState.busy
    readonly property string localStage: localActive ? (activeLocalService ? activeLocalService.operationStage : onlineState.localStage || "") : ""
    readonly property var bytes: onlineState.progress || ({})
    readonly property bool measured: !localActive && onlineActive && !onlineState.cancelling && onlineState.stage === "downloading"
        && typeof bytes.totalBytes === "number" && bytes.totalBytes > 0
        && Number.isFinite(bytes.totalBytes) && Number.isFinite(bytes.receivedBytes)
        && typeof bytes.receivedBytes === "number" && bytes.receivedBytes >= 0 && bytes.receivedBytes < bytes.totalBytes
    readonly property int percent: measured ? Math.floor(bytes.receivedBytes / bytes.totalBytes * 100) : 0
    readonly property string phase: {
        if (localActive) {
            const labels = {"inspect": "校验图包", "install": "解压并导入", "refresh-after-write": "核对安装结果",
                "launch-import": "准备退出后导入", "cancelling": "正在取消",
                "list": "读取本地图包清单", "refresh": "核对本地资源", "inspect-remove": "核对移除范围",
                "recovery-history": "读取恢复记录", "inspect-recovery": "核对恢复范围", "recover": "恢复本地资源",
                "manage-refresh": "读取已安装图包", "manage-inspect": "核对删除范围", "manage-remove": "删除图包并清理临时文件",
                "manage-refresh-after-remove": "核对删除结果"}
            return labels[localStage] || "正在处理图包"
        }
        if (onlineState.cancelling) return "正在取消下载"
        if (onlineState.stage === "checking") return "读取在线列表"
        if (measured) return "下载图包 · " + percent + "%"
        return bytes.totalBytes > 0 && bytes.receivedBytes >= bytes.totalBytes
            ? "下载数据已接收，正在校验" : "连接并下载图包"
    }
    objectName: "packageProgressCard"
    visible: localActive || onlineActive
    implicitHeight: visible ? contents.implicitHeight + 24 : 0
    radius: 12; color: "#edf4ff"; border.color: "#b9d2f5"
    Accessible.role: Accessible.Indicator
    Accessible.name: phase
    ColumnLayout {
        id: contents
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 12; spacing: 6
        Label { text: card.phase; font.bold: true; font.pixelSize: 15; color: "#17365e"; Layout.fillWidth: true; wrapMode: Text.Wrap }
        ProgressBar {
            objectName: "packageProgressBar"
            Layout.fillWidth: true
            from: 0; to: 100; value: card.percent
            indeterminate: card.visible && !card.measured
            Accessible.name: card.phase
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12; color: "#354f75"
            text: card.localActive ? (card.localStage === "install" ? "正在解压、校验并写入文件，完成后还会核对安装结果。" : (card.activeLocalService ? card.activeLocalService.message : card.onlineState.localMessage || ""))
                : card.measured ? (card.bytes.receivedBytes / 1048576).toFixed(1) + " / " + (card.bytes.totalBytes / 1048576).toFixed(1) + " MiB · 仅为下载进度，尚未完成导入。"
                : "此阶段无法计算准确百分比，请等待处理结果。"
        }
    }
}
