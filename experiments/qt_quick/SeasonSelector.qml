import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    property var seasons: []
    property string season: ""
    property string mode: ""
    property bool allowCurrentContextRequest: false
    // Gallery-only source category; it never appears in live mode selectors.
    property bool browseDifficulty2: false
    property string nightmareObjectName: objectName + "Nightmare"
    property string difficultObjectName: objectName + "Difficult"
    signal contextRequested(string season, string mode)
    // Keep confirmed season names readable in the narrow floating controls.
    readonly property real seasonMinimumWidth: {
        var textWidth = seasonFontMetrics.advanceWidth("选择赛季")
        for (var i = 0; i < seasons.length; i++)
            textWidth = Math.max(textWidth, seasonFontMetrics.advanceWidth(seasons[i].label))
        return Math.max(130, Math.ceil(textWidth) + seasonPicker.leftPadding + seasonPicker.rightPadding + 2)
    }
    readonly property bool ordinaryAvailable: season === "s1" && browseDifficulty2
    readonly property int modeCount: ordinaryAvailable ? 3 : 2
    readonly property real singleRowWidth: seasonMinimumWidth + modeCount * 64 + modeCount * columnSpacing
    readonly property bool stacked: width < singleRowWidth
    // Parent layouts must be allowed to shrink below the single-row minimum;
    // otherwise they force that width before the responsive breakpoint can fire.
    Layout.fillWidth: true
    Layout.minimumWidth: Math.max(seasonMinimumWidth, modeCount * 64 + (modeCount - 1) * columnSpacing)
    Layout.preferredWidth: singleRowWidth
    readonly property int columns: stacked ? modeCount : modeCount + 1
    property real columnSpacing: 6
    property real rowSpacing: 6
    implicitWidth: singleRowWidth
    implicitHeight: selectorGrid.implicitHeight
    FontMetrics { id: seasonFontMetrics; font: seasonPicker.font }

    function seasonIndex(identity) {
        for (var i = 0; i < seasons.length; i++)
            if (seasons[i].id === identity) return i
        return -1
    }
    function seasonLabel(identity) {
        for (var i = 0; i < seasons.length; i++)
            if (seasons[i].id === identity) return seasons[i].label
        return String(identity).toUpperCase()
    }
    function registeredContext(identity, difficulty) {
        for (var i = 0; i < seasons.length; i++) {
            if (seasons[i].id === identity) {
                var capabilities = seasons[i].capabilities || {}
                return !!capabilities[difficulty] && capabilities[difficulty].registered === true
            }
        }
        return false
    }

    function requestContext(nextSeason, nextMode) {
        for (var target = 0; target < seasons.length; target++) {
            if (seasons[target].id === nextSeason) {
                var caps = seasons[target].capabilities || {}
                // A target season without ordinary mode uses the application's
                // existing default. This is UI choice, never a map-floor guess.
                if (nextMode === "difficulty2" && !caps.difficulty2 && nextSeason !== "s1") nextMode = "nightmare"
            }
        }
        if (!enabled || (nextSeason === season && nextMode === mode && !allowCurrentContextRequest)) return
        if (nextMode !== "nightmare" && nextMode !== "difficult"
                && !(browseDifficulty2 && nextSeason === "s1" && nextMode === "difficulty2")) return
        for (var i = 0; i < seasons.length; i++) {
            if (seasons[i].id === nextSeason) {
                contextRequested(nextSeason, nextMode)
                return
            }
        }
    }

    // The outer item owns the width assigned by its parent. Changing a grid's
    // own column/span bindings while its parent is arranging that same grid
    // can synchronously re-enter Qt Quick Layouts (notably Windows font sizes).
    // Keep stable outer width hints and arrange only this inner layout.
    GridLayout {
        id: selectorGrid
        anchors.left: parent.left; anchors.right: parent.right
        columns: root.columns
        columnSpacing: root.columnSpacing; rowSpacing: root.rowSpacing
        ComboBox {
            id: seasonPicker
            objectName: root.objectName + "Season"
            Layout.fillWidth: true; Layout.minimumWidth: root.seasonMinimumWidth; Layout.preferredWidth: root.seasonMinimumWidth
            Layout.columnSpan: root.stacked ? root.columns : 1
            model: root.seasons; textRole: "label"; valueRole: "id"
            currentIndex: root.seasonIndex(root.season)
            displayText: currentIndex < 0 ? "选择赛季" : currentText
            Accessible.name: "赛季"
            delegate: ItemDelegate {
                required property var modelData
                required property int index
                objectName: root.objectName + "SeasonOption_" + modelData.id
                width: seasonPicker.width
                text: modelData.label
                highlighted: seasonPicker.highlightedIndex === index
            }
            onActivated: function(index) {
                if (index >= 0 && index < root.seasons.length)
                    root.requestContext(root.seasons[index].id, root.mode)
                // Selection is a request. A rejected or asynchronous runtime change
                // must not leave the combo claiming an unapplied season.
                currentIndex = Qt.binding(function() { return root.seasonIndex(root.season) })
            }
        }
        AppButton {
            objectName: root.nightmareObjectName
            text: "噩梦"; implicitWidth: 64; implicitHeight: 34
            selected: root.mode === "nightmare"
            Accessible.name: text + (selected ? " · 已选" : "")
            onClicked: root.requestContext(root.season, "nightmare")
        }
        AppButton {
            objectName: root.difficultObjectName
            text: "困难"; implicitWidth: 64; implicitHeight: 34
            selected: root.mode === "difficult"
            Accessible.name: text + (selected ? " · 已选" : "")
            onClicked: root.requestContext(root.season, "difficult")
        }
        AppButton {
            id: ordinaryButton
            objectName: root.objectName + "Difficulty2"
            visible: root.ordinaryAvailable
            text: "普通"; implicitWidth: 64; implicitHeight: 34
            selected: root.mode === "difficulty2"
            Accessible.name: text + (selected ? " · 已选" : "")
            onClicked: root.requestContext(root.season, "difficulty2")
        }
    }
}
