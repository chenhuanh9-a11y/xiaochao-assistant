// Window-owned notice: independent of navigation and never activates the window.
import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: notice
    property var hostWindow: null
    property bool blocked: false
    property var pending: ({})
    readonly property bool canPresent: !!hostWindow && hostWindow.visible
        && hostWindow.visibility !== Window.Minimized && !blocked

    function showPending() {
        if (!canPresent || dialog.visible || !pending.announcementId || infoService.checking) return
        if (!infoService.shouldShowAnnouncement(pending.announcementId)) {
            pending = ({})
            return
        }
        dialog.manualRequest = false
        dialog.waitingForRelease = false
        dialog.releaseData = pending
        pending = ({})
        dialog.open()
    }
    function presentContent() {
        if (dialog.opened && canPresent && dialog.releaseData.announcementId && !dialog.presented) {
            dialog.presented = true
            infoService.announcementShown(dialog.releaseData.announcementId)
        }
    }
    function openRequested(release) {
        // A hidden/closing window is never restored for a notice. Repeated
        // requests also cannot replace the snapshot currently being read.
        if (!canPresent || dialog.visible) return
        dialog.manualRequest = true
        dialog.waitingForRelease = !release.announcementId
        dialog.releaseData = release
        dialog.open()
    }
    function refreshRequested() {
        if (dialog.visible && canPresent && dialog.waitingForRelease && infoService.release.announcementId) {
            dialog.waitingForRelease = false
            dialog.releaseData = infoService.release
            dialog.resetScroll()
            presentContent()
        }
    }
    function reconcilePresentation() {
        if (!canPresent && dialog.visible) {
            // A notice hidden before opening has not been shown or read.
            if (!dialog.manualRequest && !dialog.presented && !pending.announcementId) pending = dialog.releaseData
            dialog.close()
        }
        refreshRequested()
        showPending()
    }
    // Popup focus changes can notify coordinator bindings again. Never mutate
    // the popup inside a visibility/blocking binding evaluation; recheck the
    // settled close/visibility state on the next event-loop turn instead.
    onCanPresentChanged: Qt.callLater(notice.reconcilePresentation)
    Connections {
        target: infoService
        function onStartupAnnouncement(release) { notice.pending = release; Qt.callLater(notice.reconcilePresentation) }
        function onAnnouncementRequested(release) { notice.openRequested(release) }
        function onChanged() { Qt.callLater(notice.reconcilePresentation) }
    }
    Dialog {
        id: dialog
        objectName: "startupUpdateAnnouncement"
        property var releaseData: ({})
        property bool presented: false
        property bool manualRequest: false
        property bool waitingForRelease: false
        property string scrollIdentity: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(540, parent ? parent.width - 40 : 540)
        modal: true; focus: true
        title: releaseData.announcementId
            ? (releaseData.updateAvailable ? "发现新版本 v" : "更新公告 v") + (releaseData.version || "")
            : "公告"
        header: Label {
            objectName: "startupAnnouncementTitle"
            text: dialog.title; textFormat: Text.PlainText
            padding: 16; font.pixelSize: 16; font.bold: true; color: "#17365e"
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        closePolicy: Popup.CloseOnEscape
        function resetScroll() {
            if (scrollIdentity !== releaseData.announcementId) {
                bodyScroll.contentItem.contentY = 0
                scrollIdentity = releaseData.announcementId || ""
            }
        }
        onAboutToShow: { presented = false; resetScroll() }
        onOpened: {
            if (!notice.canPresent) { close(); return }
            notice.presentContent()
        }
        onClosed: {
            waitingForRelease = false
            manualRequest = false
            Qt.callLater(notice.reconcilePresentation)
        }
        background: Rectangle { radius: 12; color: "white"; border.color: "#d5e2f5" }
        contentItem: ColumnLayout {
            spacing: 14
            Label { visible: !!dialog.releaseData.announcementId; text: "当前 " + infoService.version + " · " + (dialog.releaseData.published_date || ""); color: "#567096"; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
            ScrollView {
                id: bodyScroll; objectName: "startupAnnouncementBody"
                visible: !!dialog.releaseData.announcementId
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(220, notice.hostWindow ? notice.hostWindow.height - 240 : 220)
                clip: true; contentWidth: availableWidth
                Label { objectName: "startupAnnouncementNotes"; width: parent.width; text: dialog.releaseData.notes || "本次发布暂无公告正文。"; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: "#354f75" }
            }
            BusyIndicator {
                objectName: "announcementLoading"
                visible: dialog.waitingForRelease && infoService.checking; running: visible
                Layout.alignment: Qt.AlignHCenter
            }
            Label {
                objectName: "announcementLoadStatus"
                visible: dialog.waitingForRelease
                Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: "#567096"
                text: infoService.checking ? "正在读取公告…"
                    : infoService.updatePhase === "error" ? infoService.message.replace("检查更新失败：", "公告读取失败：")
                    : "正在等待读取公告…"
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { objectName: "startupAnnouncementQuark"; text: "夸克网盘下载"; primary: true; visible: !!dialog.releaseData.updateAvailable && !!(dialog.releaseData.pages && dialog.releaseData.pages.quark); enabled: !infoService.checking; onClicked: { infoService.openAnnouncementDownload(dialog.releaseData.announcementId, "quark"); dialog.close() } }
                AppButton { objectName: "startupAnnouncementUc"; text: "UC 网盘下载"; visible: !!dialog.releaseData.updateAvailable && !!(dialog.releaseData.pages && dialog.releaseData.pages.uc); enabled: !infoService.checking; onClicked: { infoService.openAnnouncementDownload(dialog.releaseData.announcementId, "uc"); dialog.close() } }
                AppButton { objectName: "announcementRetry"; text: "重试"; visible: dialog.waitingForRelease && infoService.updatePhase === "error"; enabled: !infoService.checking; onClicked: infoService.openAnnouncement() }
                AppButton { objectName: "startupUpdateLater"; text: dialog.waitingForRelease ? "关闭" : "知道了"; onClicked: dialog.close() }
            }
        }
    }
}
