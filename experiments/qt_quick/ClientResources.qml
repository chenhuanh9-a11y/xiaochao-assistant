import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: panel
    implicitHeight: 330
    property string confirmationText: ""
    property var packageManager: typeof packageManagerService !== "undefined" ? packageManagerService : null
    property var confirmationOwner: resourceService
    property bool managerRefreshPending: false
    // Persistent hidden pages must not turn construction into a full audit.
    property bool active: false
    property bool refreshAllowed: true
    property bool activationRefreshPending: false
    property bool pageReady: false
    property bool recoveryHistoryVisible: false
    property bool historyOpenRequested: false
    onActiveChanged: {
        activationRefreshPending = active
        if (!active) {
            dismissHistoryView()
            historyDialog.close()
        }
    }
    Timer {
        interval: 120
        running: panel.pageReady && panel.active && panel.activationRefreshPending
                 && panel.refreshAllowed && !panel.operationPending && !panel.recoveryPending
        onTriggered: panel.listPackages()
    }
    signal importSelectionCancelled()
    function dismissHistoryView() {
        historyOpenRequested = false
        if (packageManager && confirmationOwner === packageManager
                && packageManager.pendingKind === "restore-author-version") confirmation.reject()
        if (packageManager) packageManager.dismissHistory()
    }
    readonly property bool operationPending: archiveDialog.visible || resourceService.busy || resourceService.pendingKind !== "" || (packageManager && (packageManager.busy || packageManager.pendingKind !== ""))
    readonly property bool recoveryPending: resourceService.recoveryRequired || (packageManager && packageManager.recoveryRequired)
    readonly property var visibleResourceRows: resourceService.packageRows.map(function(row) {
        if (resourceService.inventoryVerified || (row.status !== "bundled" && row.status !== "installed" && row.status !== "partially-compatible" && row.status !== "incompatible")) return row
        // Previous successful checks are presentation history, not current
        // content evidence. Drop their verified counts as well as the label.
        return {packageId: row.packageId, title: row.title, builtin: row.builtin === true || row.status === "bundled",
                status: "declared", canRemove: false, verified: false}
    })

    // Both persistent pages share this single chooser and confirmation owner.
    // Capture the browsing season before opening, never the running game season.
    function openImport(season) {
        if (panel.operationPending || panel.recoveryPending) return
        archiveDialog.targetSeason = season
        archiveDialog.open()
    }

    function removePackage(packageId) {
        if (!panel.packageManager || !packageId || panel.operationPending || panel.recoveryPending) return
        panel.packageManager.requestRemove(packageId)
    }

    function listPackages() {
        activationRefreshPending = false
        managerRefreshPending = false
        resourceService.listPackages()
        managerRefreshPending = !!packageManager && resourceService.busy
    }

    function refreshPackages() {
        activationRefreshPending = false
        managerRefreshPending = false
        resourceService.refresh()
        // Only a started read owns the follow-up management scan. A lease
        // refusal must not silently enqueue another hidden reader.
        managerRefreshPending = !!packageManager && resourceService.busy
    }

    function stateLabel(status) {
        const labels = {"declared": "待核对", "history-unchecked": "历史版本待核对", "installed": "已安装", "partially-compatible": "部分层可用", "incompatible": "不兼容", "not-installed": "未安装", "incomplete": "文件缺失或已修改",
            "unmanaged": "来源未确认", "unverified": "待核对", "recovery-required": "操作未完成", "managed-remnants": "有残留文件", "bundled": "内置默认资源"}
        return labels[status] || "待核对"
    }
    function stateDetail(row) {
        if (row.compatibilityNotice) return row.compatibilityNotice
        if (row.status === "recovery-required") return "请先处理上次未完成的操作。"
        if (row.builtin && row.status !== "bundled" && row.status !== "declared") return "请重新安装以修复缺失资源。"
        if (row.status === "incomplete") return "已修改或来源不明的文件会保留。"
        if (row.status === "unmanaged") return "来源不明的文件不会删除。"
        return ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "本地图包"; color: "#142b4b"; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
            Label { text: typeof backend !== "undefined" && backend ? backend.season.toUpperCase() : ""; color: "#526785"; font.pixelSize: 14 }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            AppButton { objectName: "downloadPackagesButton"; text: "下载图包"; onClicked: clientCoordinator.navigate("downloads") }
            AppButton { objectName: "importPackagesButton"; text: "导入图包"; primary: true; enabled: !panel.operationPending && !panel.recoveryPending; onClicked: panel.openImport(backend.season) }
            AppButton {
                id: moreButton
                objectName: "resourceMoreButton"; text: "更多"; enabled: !panel.operationPending; onClicked: moreMenu.open()
                Popup {
                    id: moreMenu
                    objectName: "resourceMoreMenu"
                    focus: true; modal: true; dim: false
                    onOpened: (checkPackagesAction.enabled ? checkPackagesAction : recoveryHistoryAction).forceActiveFocus()
                    y: moreButton.height + 4; padding: 8
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                    contentItem: ColumnLayout {
                        spacing: 4
                        AppButton { id: checkPackagesAction; objectName: "checkPackagesButton"; Layout.fillWidth: true; text: "检查图包完整性"; enabled: !panel.operationPending && !panel.recoveryPending; onClicked: { moreMenu.close(); panel.refreshPackages() } }
                        AppButton { id: recoveryHistoryAction; objectName: "recoveryHistoryButton"; Layout.fillWidth: true; text: "查看恢复记录"; enabled: !panel.operationPending; onClicked: { moreMenu.close(); panel.recoveryHistoryVisible = true; resourceService.inspectRecovery() } }
                        AppButton { objectName: "clearManagedPackagesButton"; Layout.fillWidth: true; text: "清理全部图包"; enabled: !!panel.packageManager && !panel.operationPending && !panel.recoveryPending; onClicked: { moreMenu.close(); panel.packageManager.requestClearAll() } }
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: panel.recoveryPending || (panel.recoveryHistoryVisible && resourceService.recoveryTransactions.length > 0)
            Label { text: panel.recoveryPending ? "上次操作未完成" : "恢复记录"; color: "#17365e"; font.bold: true }
            Label { text: resourceService.recoveryUsesHelper ? "处理时将退出星图；完成后请重新打开。" : resourceService.recoveryExecutionAllowed ? "选择需要处理的记录。" : "请结束本局并重启星图后处理。"; color: "#526785"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            AppButton { text: "处理上次未完成的操作"; visible: resourceService.recoveryRequired; enabled: resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""; onClicked: resourceService.requestRecovery() }
            ListView {
                id: transactions
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 156)
                clip: true
                ScrollBar.vertical: ScrollBar {}
                model: resourceService.recoveryTransactions
                delegate: RowLayout {
                    required property var modelData
                    width: transactions.width
                    Label { Layout.fillWidth: true; text: resourceService.packageTitle(modelData.packageId) + " · " + resourceService.recoveryStatusLabel(modelData.status); color: "#526785"; elide: Text.ElideRight }
                    AppButton {
                        objectName: "resourceTransactionAction-" + modelData.transactionId
                        readonly property bool cleanup: modelData.status === "upgrade-cleanup-available" || modelData.status === "upgrade-cleanup-pending"
                        text: cleanup ? "清理临时副本" : "恢复图包"
                        enabled: (cleanup || modelData.pending) && resourceService.recoveryExecutionAllowed && !resourceService.busy && resourceService.pendingKind === ""
                        onClicked: resourceService.requestCleanup(modelData.transactionId)
                    }
                }
            }
        }
        ClientPackageProgress {
            Layout.fillWidth: true
            localService: resourceService
            managementService: panel.packageManager
        }
        // The package list sits on one white card like the other pages.
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 150
            radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
        ListView {
            id: packages
            objectName: "managedPackageList"
            anchors.fill: parent; anchors.margins: 4; anchors.leftMargin: 18; anchors.rightMargin: 12
            clip: true; spacing: 0
            model: panel.packageManager ? panel.packageManager.packageRows.map(function(row) {
                const checked = resourceService.inventoryVerified && panel.visibleResourceRows.find(function(item) { return item.packageId === row.packageId })
                return checked ? Object.assign({}, row, checked, {canRemove: false, canInspectRemoval: row.canInspectRemoval}) : row
            }).concat(panel.visibleResourceRows.filter(function(row) { return row.status === "bundled" || row.builtin === true })) : panel.visibleResourceRows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                required property var modelData
                required property int index
                width: packages.width; height: Math.max(70, info.implicitHeight + 22)
                RowLayout {
                    anchors.fill: parent; anchors.topMargin: 10; anchors.bottomMargin: 10; spacing: 18
                    ColumnLayout {
                        id: info
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: modelData.title; elide: Text.ElideRight; font.pixelSize: 14; color: "#142b4b"; font.bold: true }
                        Label {
                            Layout.fillWidth: true
                            objectName: "packageStatusLabel-" + modelData.packageId
                            text: panel.stateLabel(modelData.status)
                            color: modelData.status === "installed" ? "#17775a" : "#526785"; font.pixelSize: 14; wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true; visible: text.length > 0
                            text: panel.stateDetail(modelData); color: "#526785"; font.pixelSize: 14; wrapMode: Text.Wrap
                        }
                    }
                    AppButton {
                        objectName: "packageHistoryButton-" + modelData.packageId
                        text: "历史版本"; visible: !!modelData.storageFormat
                        enabled: !!panel.packageManager && !panel.operationPending && !panel.recoveryPending
                        Accessible.name: "查看作者历史版本 " + modelData.title
                        onClicked: {
                            panel.historyOpenRequested = true
                            panel.packageManager.requestHistory(modelData.packageId)
                        }
                    }
                    AppButton {
                        objectName: "removePackageButton-" + modelData.packageId
                        text: modelData.canInspectRemoval ? "检查并删除" : "删除图包"; enabled: !!panel.packageManager && (!!modelData.canRemove || !!modelData.canInspectRemoval) && !panel.operationPending && !panel.recoveryPending
                        Accessible.name: "删除图包 " + modelData.title
                        onClicked: panel.removePackage(modelData.packageId)
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#edf1f7"; visible: index < packages.count - 1 }
            }
            Label {
                anchors.centerIn: parent; width: Math.min(parent.width, 440); horizontalAlignment: Text.AlignHCenter
                visible: packages.count === 0
                text: panel.operationPending ? "正在核对本地资源…" : panel.recoveryPending ? "请先处理上次未完成的操作。" : "暂无图包，请下载或导入。"
                wrapMode: Text.Wrap; color: "#526785"; font.pixelSize: 13
            }
        }
        }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true; visible: !panel.operationPending && text.length > 0; text: (panel.packageManager ? panel.packageManager.message + "\n" : "") + resourceService.message; wrapMode: Text.Wrap
                color: panel.recoveryPending ? "#b8364a" : "#354f75"; font.pixelSize: 14
                Accessible.role: Accessible.StaticText
            }
            AppButton { text: "取消操作"; visible: resourceService.canCancel; onClicked: resourceService.cancel() }
            AppButton { text: "取消操作"; visible: !!panel.packageManager && panel.packageManager.canCancel; onClicked: panel.packageManager.cancel() }
        }
    }

    FileDialog {
        id: archiveDialog
        objectName: "resourceArchiveDialog"
        property string targetSeason: "s0"
        title: "选择 " + targetSeason.toUpperCase() + " 本地地图资源包"
        nameFilters: ["地图资源包 (*.zip)"]
        onAccepted: resourceService.importArchive(selectedFile.toString(), targetSeason)
        onRejected: panel.importSelectionCancelled()
    }
    Dialog {
        id: historyDialog
        objectName: "resourceHistoryDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(600, parent ? parent.width - 48 : 600)
        height: Math.min(460, parent ? parent.height - 48 : 460)
        title: "作者历史版本"
        modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        onClosed: {
            panel.dismissHistoryView()
        }
        contentItem: ColumnLayout {
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: "历史列表只核对来源记录；点击回退后会核对完整图片和当前底模。回退只切换作者显示，不回退程序或识别底模。"
            }
            ListView {
                id: historyList
                objectName: "authorHistoryList"
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; spacing: 8
                ScrollBar.vertical: ScrollBar {}
                model: panel.packageManager ? panel.packageManager.historyRows : []
                delegate: RowLayout {
                    required property var modelData
                    width: historyList.width
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: modelData.snapshotId.slice(0, 12) + " · " + (modelData.current ? "当前版本" : modelData.status === "unverified-history" ? "来源或记录不完整，不可回退" : "保留版本，内容待核对")
                              + (modelData.archiveSha256 ? "\n图包 SHA256：" + modelData.archiveSha256.slice(0, 12) : "")
                    }
                    AppButton {
                        objectName: "restoreAuthorVersion-" + modelData.snapshotId
                        text: "核对并回退"
                        enabled: modelData.canInspectRestore && !panel.operationPending && !panel.recoveryPending
                        Accessible.name: "核对并回退作者版本 " + modelData.snapshotId.slice(0, 12)
                        onClicked: panel.packageManager.requestRestoreVersion(modelData.packageId, modelData.sourceTransactionId)
                    }
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                visible: !panel.packageManager || panel.packageManager.historyRows.length === 0
                text: "当前没有已核对的历史记录，或资源已改变。可点击重新核对。"
            }
        }
        footer: DialogButtonBox {
            AppButton {
                objectName: "refreshAuthorHistoryButton"; text: "重新核对"
                enabled: !!panel.packageManager && !panel.operationPending && !panel.recoveryPending
                onClicked: panel.packageManager.requestHistory(panel.packageManager.historyPackageId)
            }
            AppButton { objectName: "closeAuthorHistoryButton"; text: "关闭"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            onRejected: historyDialog.close()
        }
    }
    Dialog {
        id: confirmation
        objectName: "resourceConfirmationDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 48 : 520)
        modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        contentItem: Label { text: panel.confirmationText; wrapMode: Text.Wrap; color: "#354963"; font.pixelSize: 13; padding: 6 }
        footer: DialogButtonBox {
            AppButton { objectName: "resourceConfirmCancelButton"; text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            AppButton { objectName: "resourceConfirmAcceptButton"; text: "确认"; primary: true; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            onAccepted: confirmation.accept()
            onRejected: confirmation.reject()
        }
        onAccepted: panel.confirmationOwner.confirmOperation(true)
        onRejected: panel.confirmationOwner.confirmOperation(false)
        onClosed: if (panel.confirmationOwner.pendingKind !== "") panel.confirmationOwner.confirmOperation(false)
    }
    Connections {
        target: resourceService
        function onChanged() {
            if (panel.managerRefreshPending && !resourceService.busy && resourceService.pendingKind === "") {
                panel.managerRefreshPending = false
                panel.packageManager.refresh()
            }
        }
        function onConfirmationRequested(title, message) {
            panel.confirmationOwner = resourceService
            confirmation.title = title
            panel.confirmationText = message
            confirmation.open()
        }
    }
    Connections {
        target: panel.packageManager
        function onHistoryReady(packageId) {
            if (panel.active && panel.historyOpenRequested) historyDialog.open()
        }
        function onConfirmationRequested(title, message) {
            panel.confirmationOwner = panel.packageManager
            confirmation.title = title
            panel.confirmationText = message
            confirmation.open()
        }
    }
    Component.onCompleted: {
        pageReady = true
        activationRefreshPending = active
    }
}
