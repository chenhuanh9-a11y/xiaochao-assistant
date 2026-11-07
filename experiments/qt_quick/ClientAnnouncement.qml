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
        dialog.releaseData = pending
        pending = ({})
        dialog.open()
    }
    onCanPresentChanged: {
        if (!canPresent && dialog.visible) {
            // A notice hidden before opening has not been shown or read.
            if (!dialog.presented && !pending.announcementId) pending = dialog.releaseData
            dialog.close()
        }
        showPending()
    }
    Connections {
        target: infoService
        function onStartupAnnouncement(release) { notice.pending = release; notice.showPending() }
        function onChanged() { notice.showPending() }
    }
    Dialog {
        id: dialog
        objectName: "startupUpdateAnnouncement"
        property var releaseData: ({})
        property bool presented: false
        property string scrollIdentity: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(540, parent ? parent.width - 40 : 540)
        modal: true; focus: true
        title: (releaseData.updateAvailable ? "发现新版本 v" : "更新公告 v") + (releaseData.version || "")
        header: Label {
            objectName: "startupAnnouncementTitle"
            text: dialog.title; textFormat: Text.PlainText
            padding: 16; font.pixelSize: 16; font.bold: true; color: "#17365e"
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        closePolicy: Popup.CloseOnEscape
        onAboutToShow: {
            presented = false
            if (scrollIdentity !== releaseData.announcementId) {
                bodyScroll.contentItem.contentY = 0
                scrollIdentity = releaseData.announcementId
            }
        }
        onOpened: {
            if (!notice.canPresent) { close(); return }
            presented = true
            infoService.announcementShown(releaseData.announcementId)
        }
        onClosed: Qt.callLater(notice.showPending)
        background: Rectangle { radius: 12; color: "white"; border.color: "#d5e2f5" }
        contentItem: ColumnLayout {
            spacing: 14
            Label { text: "当前 " + infoService.version + " · " + (dialog.releaseData.published_date || ""); color: "#567096"; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
            ScrollView {
                id: bodyScroll; objectName: "startupAnnouncementBody"
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(220, notice.hostWindow ? notice.hostWindow.height - 240 : 220)
                clip: true; contentWidth: availableWidth
                Label { objectName: "startupAnnouncementNotes"; width: parent.width; text: dialog.releaseData.notes || ""; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: "#354f75" }
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { objectName: "startupAnnouncementQuark"; text: "夸克网盘下载"; primary: true; visible: !!dialog.releaseData.updateAvailable && !!(dialog.releaseData.pages && dialog.releaseData.pages.quark); enabled: !infoService.checking; onClicked: { infoService.openAnnouncementDownload(dialog.releaseData.announcementId, "quark"); dialog.close() } }
                AppButton { objectName: "startupAnnouncementUc"; text: "UC 网盘下载"; visible: !!dialog.releaseData.updateAvailable && !!(dialog.releaseData.pages && dialog.releaseData.pages.uc); enabled: !infoService.checking; onClicked: { infoService.openAnnouncementDownload(dialog.releaseData.announcementId, "uc"); dialog.close() } }
                AppButton { objectName: "startupUpdateLater"; text: "知道了"; onClicked: dialog.close() }
            }
        }
    }
}
