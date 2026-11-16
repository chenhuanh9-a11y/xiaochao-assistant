import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

Item {
    id: panel
    property bool active: false
    property bool refreshAllowed: true
    property bool refreshPending: false
    signal importSelectionCancelled()
    readonly property bool operationPending: archiveDialog.visible || legacyDialog.visible || resourceService.busy || resourceService.activationPlanOpen || resourceService.removalPlanOpen
    readonly property bool recoveryPending: resourceService.recoveryRequired
    readonly property var hostWindow: Window.window
    readonly property bool planVisible: hostWindow !== null && hostWindow.visible
                                        && hostWindow.visibility !== Window.Minimized
    readonly property bool importedPackages: true
    readonly property var packageManager: resourceService
    function removePackage(key) {
        if (!operationPending && !recoveryPending) resourceService.requestRemoval(key)
    }
    onActiveChanged: {
        if (active) refreshPending = true
        else resourceService.dismissActivationPlan()
    }
    onPlanVisibleChanged: { syncActivationDialog(); syncRemovalDialog() }
    function syncActivationDialog() {
        if (resourceService.activationPlanOpen && planVisible) {
            if (!activationDialog.visible) activationDialog.open()
        } else if (activationDialog.visible) activationDialog.close()
    }
    function syncRemovalDialog() {
        if (resourceService.removalPlanOpen && hostWindow !== null && hostWindow.visible
                && hostWindow.visibility !== Window.Minimized) {
            if (!removalDialog.visible) removalDialog.open()
        } else if (removalDialog.visible) removalDialog.close()
    }
    Connections {
        target: resourceService
        function onChanged() { panel.syncRemovalDialog() }
    }
    Connections {
        target: panel.hostWindow
        function onVisibleChanged() { panel.syncRemovalDialog() }
        function onVisibilityChanged() { panel.syncRemovalDialog() }
    }
    function openImport(season) {
        if (!operationPending) archiveDialog.open()
    }
    Timer {
        interval: 120
        running: panel.active && panel.refreshPending && !panel.operationPending
        onTriggered: {
            panel.refreshPending = false
            if (!resourceService.inventoryLoaded) resourceService.listPackages()
        }
    }
    function statusText(row) {
        if (row.status === "selected") return "已选用 · " + row.selectedContexts.join("、")
        if (row.status === "saved") return "文件已校验 · 尚未启用"
        if (row.status === "damaged") return "文件或版本记录异常，请重新获取资源包"
        return "已保存 · 内容待校验，尚未启用"
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        Label { text: "本地资源"; color: "#142b4b"; font.pixelSize: 18; font.bold: true }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#354f75"; font.pixelSize: 14
            text: "结构包（.xingtu-base）包含楼层控件，导入后自动启用对应模式。请先导入结构包，再导入作者路线包（.xingtu-route）；路线导入后会自动加入地图资源，可直接预览和选择作者。"
        }
        Label {
            objectName: "resourceDirectoryLabel"
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap
            text: "资源目录：" + resourceService.resourceDirectory
            color: "#526785"; font.pixelSize: 12
        }
        RowLayout {
            visible: resourceService.legacyResourceDirectory.length > 0
            Layout.fillWidth: true; spacing: 8
            Label { text: "发现旧版公共资源"; color: "#354f75"; font.pixelSize: 13 }
            AppButton {
                objectName: "migrateLegacyResourcesButton"; text: "迁入软件目录"; primary: true
                enabled: resourceService.canMigrateLegacyResources && panel.refreshAllowed && !panel.operationPending
                onClicked: { legacyDialog.cleanup = false; legacyDialog.open() }
            }
            AppButton {
                objectName: "cleanupLegacyResourcesButton"; text: "清理旧副本"
                enabled: resourceService.canCleanupLegacyResources && panel.refreshAllowed && !panel.operationPending
                onClicked: { legacyDialog.cleanup = true; legacyDialog.open() }
            }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            spacing: 8; Layout.fillWidth: true
            AppButton { objectName: "importPackagesButton"; text: "导入资源包"; primary: true; enabled: !panel.operationPending; onClicked: panel.openImport("") }
            AppButton { objectName: "checkPackagesButton"; text: "检查文件完整性"; enabled: !panel.operationPending; onClicked: resourceService.refresh() }
            AppButton { objectName: "downloadPackagesButton"; text: "下载资源包"; onClicked: clientCoordinator.navigate("downloads") }
            Item { Layout.fillWidth: true }
        }
        ColumnLayout {
            visible: resourceService.recoveryRequired; Layout.fillWidth: true; spacing: 8
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "上次资源切换未完成。请先结束本局，再处理未完成的操作。"; color: "#b8364a" }
            AppButton { objectName: "recoverResourcesButton"; text: "处理未完成的切换"; enabled: panel.refreshAllowed && !panel.operationPending; onClicked: resourceService.requestRecovery() }
        }
        ClientPackageProgress { Layout.fillWidth: true; localService: resourceService }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150
            radius: 16; color: "white"; border.width: 1; border.color: "#d6e0ef"
            ListView {
                id: packages; objectName: "managedPackageList"
                anchors.fill: parent; anchors.margins: 16
                clip: true; boundsBehavior: Flickable.StopAtBounds
                model: resourceService.packageModel
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    id: packageDelegate
                    required property var modelData
                    required property int index
                    width: packages.width; height: details.implicitHeight + 24
                    ColumnLayout {
                        id: details
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.topMargin: 12
                        spacing: 5
                        Label { Layout.fillWidth: true; text: modelData.title; elide: Text.ElideRight; font.pixelSize: 15; font.bold: true; color: "#142b4b"; textFormat: Text.PlainText }
                        Label { Layout.fillWidth: true; text: "版本 " + modelData.resourceVersion; elide: Text.ElideRight; color: "#354f75"; textFormat: Text.PlainText }
                        Label { objectName: "packageStatusLabel-" + modelData.key; Layout.fillWidth: true; text: panel.statusText(modelData); wrapMode: Text.Wrap; color: modelData.status === "damaged" ? "#b8364a" : modelData.status === "selected" ? "#17775a" : "#526785" }
                        Repeater {
                            model: resourceService.activationOptions
                            delegate: ColumnLayout {
                                required property var modelData
                                visible: packageDelegate.modelData.activatableContexts.indexOf(modelData.context) >= 0
                                Layout.fillWidth: true
                                spacing: 5
                                AppButton {
                                    objectName: "activatePackageButton-" + packageDelegate.modelData.key + modelData.objectSuffix
                                    enabled: panel.refreshAllowed && !panel.operationPending && !panel.recoveryPending
                                    text: packageDelegate.modelData.selectedContexts.indexOf(modelData.context) >= 0 ? "重新校验此版本" : modelData.label
                                    onClicked: resourceService.activatePackage(packageDelegate.modelData.key, modelData.season, modelData.mode)
                                }
                                Label {
                                    objectName: "activationNotice-" + packageDelegate.modelData.key + modelData.objectSuffix
                                    visible: modelData.notice.length > 0
                                    Layout.fillWidth: true
                                    text: modelData.notice
                                    wrapMode: Text.Wrap
                                    color: "#526785"
                                }
                            }
                        }
                        Repeater {
                            model: resourceService.displayModelForPackage(packageDelegate.modelData.key)
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 5
                                AppButton {
                                    objectName: "activateDisplayButton-" + packageDelegate.modelData.key + "-" + modelData.key
                                    text: modelData.label
                                    enabled: modelData.available && panel.refreshAllowed && !panel.operationPending && !panel.recoveryPending
                                    onClicked: resourceService.activateDisplayComponent(packageDelegate.modelData.key, modelData.key)
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "作者路线仅用于显示。配准由作者提供，尚未复核。"
                                    wrapMode: Text.Wrap
                                    color: "#526785"
                                }
                            }
                        }
                        AppButton {
                            objectName: "removePackageButton-" + packageDelegate.modelData.key
                            text: "删除此资源包"
                            enabled: packageDelegate.modelData.canInspectRemoval && panel.refreshAllowed
                                && !panel.operationPending && !panel.recoveryPending
                            onClicked: panel.removePackage(packageDelegate.modelData.key)
                        }
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#edf1f7"; visible: index < packages.count - 1 }
                }
                Label {
                    anchors.centerIn: parent; width: Math.min(parent.width, 440)
                    visible: packages.count === 0; horizontalAlignment: Text.AlignHCenter
                    text: resourceService.busy ? "正在读取本地资源…" : "尚未导入资源。请先导入所需模式的基础结构包；楼层控件已随包提供。"
                    color: "#526785"; wrapMode: Text.Wrap; font.pixelSize: 14
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: resourceService.message; wrapMode: Text.Wrap; color: "#354f75"; font.pixelSize: 14; textFormat: Text.PlainText }
            AppButton { text: "取消操作"; visible: resourceService.canCancel; onClicked: resourceService.cancel() }
        }
    }
    FileDialog {
        id: archiveDialog; objectName: "resourceArchiveDialog"
        title: "选择星图结构包或作者路线包"
        nameFilters: ["星图资源包 (*.xingtu-base *.xingtu-route)",
                      "星图结构包 (*.xingtu-base)", "作者路线包 (*.xingtu-route)"]
        onAccepted: resourceService.importArchive(selectedFile.toString())
        onRejected: panel.importSelectionCancelled()
    }
    Dialog {
        id: legacyDialog; objectName: "legacyResourceLocationDialog"
        property bool cleanup: false
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(560, parent.width - 32)
        title: cleanup ? "清理旧版公共资源" : "迁移已有资源"
        modal: true; closePolicy: Popup.CloseOnEscape
        contentItem: ColumnLayout {
            spacing: 12
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                text: "旧目录：" + resourceService.legacyResourceDirectory + "\n软件资源目录：" + resourceService.resourceDirectory
                color: "#354f75"
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                text: legacyDialog.cleanup
                    ? "将核对并删除已完整迁入本安装的旧资源版本，以释放旧目录空间。其他旧版星图将无法继续使用这些副本。未知、改动或未迁入的文件会保留。"
                    : "复制并验证已有结构包、路线包和选择记录，无需重新下载。迁入需要目标磁盘有足够空间，完成后原目录仍保留；之后可明确选择清理旧副本。软件目录已有资源时不会覆盖或合并。"
                color: legacyDialog.cleanup ? "#b8364a" : "#526785"
            }
        }
        footer: DialogButtonBox {
            AppButton { text: "取消"; onClicked: legacyDialog.reject() }
            AppButton {
                objectName: "confirmLegacyResourceLocationButton"
                text: legacyDialog.cleanup ? "确认清理旧副本" : "开始迁移"; primary: true
                enabled: !resourceService.busy && panel.refreshAllowed
                onClicked: {
                    const cleanup = legacyDialog.cleanup
                    legacyDialog.close()
                    if (cleanup) resourceService.cleanupLegacyResources()
                    else resourceService.migrateLegacyResources()
                }
            }
        }
    }
    Dialog {
        id: removalDialog; objectName: "resourceRemovalPlanDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(540, parent.width - 32)
        height: Math.min(implicitHeight, parent.height - 32)
        title: "删除资源包"
        modal: true; closePolicy: Popup.CloseOnEscape
        property string displayedPlanId: ""
        onOpened: displayedPlanId = resourceService.removalPlanId
        onRejected: resourceService.cancelRemoval(displayedPlanId)
        contentItem: Flickable {
            implicitHeight: Math.min(removalContent.implicitHeight, 400)
            contentHeight: removalContent.implicitHeight; clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: removalContent
                width: parent.width; spacing: 12
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: resourceService.removalPlanSummary; color: "#354f75"
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    text: "确认后将删除此版本的本地文件。原始导入包会保留；需要时可重新导入。"
                    color: "#526785"
                }
            }
        }
        footer: DialogButtonBox {
            AppButton {
                objectName: "cancelRemovalPlanButton"; text: "取消"
                onClicked: resourceService.cancelRemoval(removalDialog.displayedPlanId)
            }
            AppButton {
                objectName: "confirmRemovalPlanButton"; text: "删除此版本"
                enabled: !resourceService.busy && panel.refreshAllowed
                onClicked: resourceService.confirmRemoval(removalDialog.displayedPlanId)
            }
        }
    }
    Dialog {
        id: activationDialog; objectName: "resourceActivationPlanDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(540, parent.width - 32)
        height: Math.min(implicitHeight, parent.height - 32)
        modal: true; closePolicy: Popup.CloseOnEscape
        title: resourceService.activationPlanTitle
        property string displayedPlanId: ""
        onOpened: displayedPlanId = resourceService.activationPlanId
        onRejected: resourceService.cancelActivationPlan(displayedPlanId)
        contentItem: Flickable {
            implicitHeight: Math.min(planContent.implicitHeight, 400)
            contentHeight: planContent.implicitHeight; clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: planContent
                width: parent.width; spacing: 12
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: "基础版本变化会影响以下作者。请选择配套版本，或明确停用该显示组件。停用只改变资源选择，文件仍保留。"
                    color: "#354f75"
                }
                Repeater {
                    model: resourceService.activationPlanModel
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 5
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            text: modelData.label; color: "#142b4b"; font.bold: true
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            text: modelData.scope; color: "#526785"
                        }
                        ComboBox {
                            objectName: "activationAuthorChoice-" + modelData.key
                            Layout.fillWidth: true
                            model: modelData.choices; textRole: "label"; valueRole: "key"
                            currentIndex: {
                                for (let i = 0; i < modelData.choices.length; ++i)
                                    if (modelData.choices[i].key === modelData.choice) return i
                                return -1
                            }
                            displayText: currentIndex < 0 ? "请选择处理方式" : currentText
                            onActivated: resourceService.chooseActivationAuthor(
                                activationDialog.displayedPlanId, modelData.key, currentValue)
                            contentItem: Text {
                                text: parent.displayText; textFormat: Text.PlainText
                                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                leftPadding: 10; rightPadding: 28; color: "#142b4b"
                            }
                            delegate: ItemDelegate {
                                required property var modelData
                                width: ListView.view.width
                                contentItem: Text {
                                    text: modelData.label; textFormat: Text.PlainText
                                    wrapMode: Text.Wrap; color: "#142b4b"
                                }
                            }
                        }
                    }
                }
                Label {
                    objectName: "activationPlanSummary"
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: resourceService.activationPlanSummary; color: "#354f75"
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    text: "配套版本将在启用时完整校验。作者配准由作者提供，尚未复核。"
                    color: "#526785"
                }
            }
        }
        footer: DialogButtonBox {
            AppButton {
                objectName: "cancelActivationPlanButton"; text: "取消"
                onClicked: resourceService.cancelActivationPlan(activationDialog.displayedPlanId)
            }
            AppButton {
                objectName: "commitActivationPlanButton"; text: "启用这些资源"; primary: true
                enabled: resourceService.canCommitActivationPlan && panel.refreshAllowed
                onClicked: resourceService.commitActivationPlan(activationDialog.displayedPlanId)
            }
        }
    }
    Connections {
        target: resourceService
        function onChanged() { panel.syncActivationDialog() }
    }
    Component.onCompleted: refreshPending = active
}
