import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

RowLayout {
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
    spacing: 6
    implicitWidth: 270

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
                return capabilities[difficulty] && capabilities[difficulty].registered === true
            }
        }
        return false
    }

    function requestContext(nextSeason, nextMode) {
        if (!enabled || (nextSeason === season && nextMode === mode && !allowCurrentContextRequest)) return
        if (nextMode === "difficulty2" && nextSeason !== "s1" && browseDifficulty2) nextMode = "difficult"
        if (nextMode !== "nightmare" && nextMode !== "difficult"
                && !(browseDifficulty2 && nextSeason === "s1" && nextMode === "difficulty2")) return
        for (var i = 0; i < seasons.length; i++) {
            if (seasons[i].id === nextSeason) {
                contextRequested(nextSeason, nextMode)
                return
            }
        }
    }

    ComboBox {
        id: seasonPicker
        objectName: root.objectName + "Season"
        Layout.fillWidth: true; Layout.minimumWidth: 82; Layout.preferredWidth: 118
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
        objectName: root.objectName + "Difficulty2"
        visible: root.browseDifficulty2 && root.season === "s1"
        text: "普通"; implicitWidth: 64; implicitHeight: 34
        selected: root.mode === "difficulty2"
        Accessible.name: text + (selected ? " · 已选" : "")
        onClicked: root.requestContext(root.season, "difficulty2")
    }
}
