import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Multibox bar widget: one icon whose color tracks fleet health, and a popup
// with a row per peer plus quick actions (pause clipboard sync, toggle
// extend-display on the desktop, open Dropzone).
//
// All state comes from `multibox status --json`; all actions shell out to the
// same CLI, so the widget stays a thin view over what the terminal offers.
BarWidget {
  id: root
  moduleName: "multibox"

  readonly property string mb: Quickshell.env("HOME") + "/.local/bin/multibox"

  property var status: null
  readonly property var peers: status && status.peers ? status.peers : []
  readonly property int upCount: {
    var n = 0
    for (var i = 0; i < peers.length; i++) if (peers[i].up === true) n++
    return n
  }
  readonly property bool isDesktop: status && status.role === "desktop"
  readonly property bool clipsyncOn: status && status.clipsync === true
  readonly property bool kvmOn: status && status.kvm === true
  readonly property bool extendLeft: status && status.extend && status.extend.left === true
  readonly property bool extendRight: status && status.extend && status.extend.right === true

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color okColor: Color.accent
  readonly property color badColor: bar ? bar.urgent : Color.urgent
  readonly property color iconColor: peers.length === 0 ? Qt.darker(fg, 1.55)
    : upCount === peers.length ? fg
    : upCount > 0 ? Qt.darker(fg, 1.55)
    : badColor

  function refresh() {
    statusProc.running = false
    statusProc.running = true
  }

  // Actions run detached; the poll that follows shortly repaints the truth.
  function act(command) {
    if (bar) bar.run(command)
    actTimer.restart()
  }

  function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'"
  }

  // Send the files behind a QML drop event to a machine; result arrives as a
  // desktop notification from `multibox send --notify`.
  function sendDrop(drop, machineName) {
    if (!drop.hasUrls) return false
    var cmd = mb + " send --notify " + shellQuote(machineName)
    var count = 0
    for (var i = 0; i < drop.urls.length; i++) {
      var url = String(drop.urls[i])
      if (url.indexOf("file://") !== 0) continue
      cmd += " " + shellQuote(decodeURIComponent(url.substring(7)))
      count++
    }
    if (count === 0) return false
    act(cmd)
    return true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Component.onCompleted: refresh()

  Process {
    id: statusProc
    command: ["bash", "-c", root.mb + " status --json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.status = JSON.parse(String(text || "")) } catch (e) { root.status = null }
      }
    }
  }

  Timer {
    interval: Math.max(3, Number(root.setting("refreshIntervalSec", 10))) * 1000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Timer {
    id: actTimer
    interval: 900
    repeat: false
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uf108"
    foreground: root.iconColor
    useActiveColor: false
    slotSize: Style.bar.iconSlot
    tooltipText: root.status
      ? (root.upCount + "/" + root.peers.length + " machines online")
      : "Multibox"

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else { root.refresh(); popup.open = !popup.open }
    }
  }

  // Dragging files over the bar icon opens the popup so its machine rows
  // can receive the drop.
  DropArea {
    anchors.fill: parent
    onEntered: function(drag) {
      if (!popup.open) { root.refresh(); popup.open = true }
    }
  }

  PopupCard {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    contentWidth: Style.space(230)
    contentHeight: fittedContentHeight(content.implicitHeight)

    Column {
      id: content
      width: parent.width
      spacing: Style.spacing.md

      PanelSectionHeader { text: "Machines" }

      Repeater {
        model: root.peers
        // Each machine row doubles as a file drop target: drag files from a
        // file manager onto the row to send them to that machine.
        delegate: Rectangle {
          id: peerRow
          required property var modelData
          width: content.width
          height: Style.spacing.popupRowHeight
          radius: Style.cornerRadius
          color: peerDrop.containsDrag
            ? Style.hoverFillFor(root.fg, Color.accent)
            : "transparent"

          Row {
            anchors.verticalCenter: parent.verticalCenter
            x: Style.spacing.sm
            spacing: Style.spacing.lg

            Rectangle {
              width: Style.space(9)
              height: width
              radius: width / 2
              anchors.verticalCenter: parent.verticalCenter
              color: peerRow.modelData.up ? root.okColor : root.badColor
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: peerRow.modelData.name
              color: root.fg
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: peerDrop.containsDrag ? "drop to send"
                : (peerRow.modelData.up ? peerRow.modelData.ip : "offline")
              color: peerDrop.containsDrag ? Color.accent : Qt.darker(root.fg, 1.55)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          DropArea {
            id: peerDrop
            anchors.fill: parent
            enabled: peerRow.modelData.up === true
            onDropped: function(drop) {
              if (root.sendDrop(drop, peerRow.modelData.name)) drop.accept()
            }
          }
        }
      }

      PanelSeparator {}

      Row {
        spacing: Style.spacing.lg
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Mouse/KB sharing"
          color: root.fg
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
        ToggleSwitch {
          anchors.verticalCenter: parent.verticalCenter
          checked: root.kvmOn
          onToggled: root.act(root.mb + " kvm " + (root.kvmOn ? "off" : "on"))
        }
      }

      Row {
        spacing: Style.spacing.lg
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Clipboard sync"
          color: root.fg
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
        ToggleSwitch {
          anchors.verticalCenter: parent.verticalCenter
          checked: root.clipsyncOn
          onToggled: root.act(root.mb + (root.clipsyncOn ? " pause" : " resume"))
        }
      }

      Row {
        visible: root.isDesktop
        spacing: Style.spacing.lg
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Extend left"
          color: root.fg
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
        ToggleSwitch {
          anchors.verticalCenter: parent.verticalCenter
          checked: root.extendLeft
          onToggled: root.act(root.mb + " extend " + (root.extendLeft ? "stop left" : "start left"))
        }
        Button {
          anchors.verticalCenter: parent.verticalCenter
          visible: root.extendLeft
          text: "Send window"
          fontSize: Style.font.caption
          tooltipText: "Move the focused window onto the left laptop screen"
          onClicked: root.act(root.mb + " send-window left")
        }
      }

      Row {
        visible: root.isDesktop
        spacing: Style.spacing.lg
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Extend right"
          color: root.fg
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
        ToggleSwitch {
          anchors.verticalCenter: parent.verticalCenter
          checked: root.extendRight
          onToggled: root.act(root.mb + " extend " + (root.extendRight ? "stop right" : "start right"))
        }
        Button {
          anchors.verticalCenter: parent.verticalCenter
          visible: root.extendRight
          text: "Send window"
          fontSize: Style.font.caption
          tooltipText: "Move the focused window onto the right laptop screen"
          onClicked: root.act(root.mb + " send-window right")
        }
      }

      PanelSeparator {}

      Row {
        spacing: Style.spacing.md

        Button {
          text: "Open Dropzone"
          onClicked: {
            root.act(root.mb + " drop")
            popup.open = false
          }
        }
        Button {
          text: "Setup…"
          onClicked: {
            root.act(root.mb + " setup")
            popup.open = false
          }
        }
      }
    }
  }

  // Shape contract for shell.summon/hide/toggle routing.
  readonly property bool opened: popup.open
  function open() { refresh(); popup.open = true }
  function close() { popup.open = false }
}
