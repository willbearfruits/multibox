#!/usr/bin/env python3
"""multibox-setup — arrange the fleet's seats and options, then apply.

A picture of the desk: drag a machine chip into the LEFT or RIGHT slot
beside the (fixed) desktop, set per-side extend-display resolutions, and
Apply. Applying rewrites ~/.config/multibox/machines.json, regenerates this
machine's configs via `multibox apply`, and saves the fleet description to
~/Drop/multibox-fleet.json so every other machine can pick it up with
`multibox apply --from-drop` (the file syncs over the existing ~/Drop share).
"""
import json
import os
import subprocess

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GObject  # noqa: E402

CONFIG = os.path.expanduser("~/.config/multibox/machines.json")
FLEET_DROP = os.path.expanduser("~/Drop/multibox-fleet.json")
RESOLUTIONS = ["1920x1080@60", "1600x900@60", "2560x1440@60", "1280x800@60"]

CSS = b"""
.seat-slot { border: 2px dashed alpha(currentColor, 0.35); border-radius: 12px;
             min-width: 150px; min-height: 110px; }
.seat-slot.hover { border-color: @accent_color; background: alpha(@accent_color, 0.08); }
.chip { border-radius: 10px; padding: 14px 10px; }
.desktop-box { border: 2px solid alpha(currentColor, 0.5); border-radius: 12px;
               min-width: 170px; min-height: 130px; }
.hint { opacity: 0.6; font-size: 0.85em; }
"""


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


class SetupWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Multibox Setup")
        self.set_default_size(640, -1)

        cfg = load_config()
        self.machines = cfg["machines"]
        self.options = cfg.get("options", {})
        self.desktop = next((m for m in self.machines if m["role"] == "desktop"), None)
        if self.desktop is None:
            self.machines[0]["role"] = "desktop"
            self.desktop = self.machines[0]
        # seat state: side -> machine name (or None)
        self.seats = {"left": None, "right": None}
        for m in self.machines:
            if m["role"] in self.seats and self.seats[m["role"]] is None:
                self.seats[m["role"]] = m["name"]

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                        margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        self.set_child(outer)

        title = Gtk.Label(label="Drag a machine into the seat where it sits on your desk")
        title.add_css_class("title-4")
        outer.append(title)

        # ---- the desk: [left slot] [desktop] [right slot] -------------------
        desk = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14,
                       halign=Gtk.Align.CENTER)
        outer.append(desk)
        self.slot_boxes = {}
        desk.append(self.make_slot("left"))
        desk.append(self.make_desktop_box())
        desk.append(self.make_slot("right"))

        # ---- unseated machines ---------------------------------------------
        self.tray_label = Gtk.Label(label="Unseated (drag into a slot):", xalign=0)
        self.tray_label.add_css_class("hint")
        outer.append(self.tray_label)
        self.tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tray_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        tray_target.connect("drop", lambda t, name, x, y: self.seat(name, None))
        self.tray.add_controller(tray_target)
        outer.append(self.tray)

        outer.append(Gtk.Separator())

        # ---- options --------------------------------------------------------
        opts_title = Gtk.Label(label="Extra-monitor resolution (extend-display)", xalign=0)
        opts_title.add_css_class("title-4")
        outer.append(opts_title)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        outer.append(grid)
        self.res_entries = {}
        for row, side in enumerate(("left", "right")):
            grid.attach(Gtk.Label(label=f"{side.capitalize()} laptop:", xalign=0), 0, row, 1, 1)
            combo = Gtk.ComboBoxText.new_with_entry()
            for r in RESOLUTIONS:
                combo.append_text(r)
            current = self.options.get("extend", {}).get(side, RESOLUTIONS[0])
            combo.get_child().set_text(current)
            grid.attach(combo, 1, row, 1, 1)
            self.res_entries[side] = combo

        hint = Gtk.Label(xalign=0, wrap=True, label=(
            "Applying updates this machine immediately and saves the fleet file to "
            "~/Drop. On each other machine run:  multibox apply --from-drop"))
        hint.add_css_class("hint")
        outer.append(hint)

        # ---- apply ----------------------------------------------------------
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.append(bottom)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        bottom.append(apply_btn)
        self.status = Gtk.Label(label="", xalign=0, hexpand=True, wrap=True)
        bottom.append(self.status)

        self.render()

    # ---- widgets -----------------------------------------------------------
    def make_chip(self, name, draggable=True):
        chip = Gtk.Button(label=name)
        chip.add_css_class("chip")
        if draggable:
            src = Gtk.DragSource()
            src.set_actions(Gdk.DragAction.MOVE)
            src.connect("prepare", lambda s, x, y:
                        Gdk.ContentProvider.new_for_value(name))
            chip.add_controller(src)
        return chip

    def make_slot(self, side):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        valign=Gtk.Align.CENTER)
        frame.add_css_class("seat-slot")
        cap = Gtk.Label(label=side.upper())
        cap.add_css_class("hint")
        frame.append(cap)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         valign=Gtk.Align.CENTER, vexpand=True)
        frame.append(holder)
        self.slot_boxes[side] = holder

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", lambda t, name, x, y, s=side: self.seat(name, s))
        target.connect("enter", lambda t, x, y, f=frame:
                       (f.add_css_class("hover"), Gdk.DragAction.MOVE)[1])
        target.connect("leave", lambda t, f=frame: f.remove_css_class("hover"))
        frame.add_controller(target)
        return frame

    def make_desktop_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      valign=Gtk.Align.CENTER)
        box.add_css_class("desktop-box")
        cap = Gtk.Label(label="DESKTOP")
        cap.add_css_class("hint")
        box.append(cap)
        name = Gtk.Label(label=self.desktop["name"])
        name.add_css_class("title-3")
        box.append(name)
        box.append(Gtk.Label(label=self.desktop["ip"]))
        return box

    # ---- state -------------------------------------------------------------
    def seat(self, name, side):
        if name == self.desktop["name"]:
            return False
        for s in self.seats:  # unseat from wherever it was
            if self.seats[s] == name:
                self.seats[s] = None
        if side is not None:
            self.seats[side] = name  # displacing any occupant back to the tray
        self.render()
        return True

    def unseated(self):
        seated = set(v for v in self.seats.values() if v)
        return [m["name"] for m in self.machines
                if m["role"] != "desktop" and m["name"] not in seated]

    def render(self):
        for side, holder in self.slot_boxes.items():
            while (c := holder.get_first_child()) is not None:
                holder.remove(c)
            if self.seats[side]:
                holder.append(self.make_chip(self.seats[side]))
            else:
                empty = Gtk.Label(label="(empty)")
                empty.add_css_class("hint")
                holder.append(empty)
        while (c := self.tray.get_first_child()) is not None:
            self.tray.remove(c)
        pending = self.unseated()
        for name in pending:
            self.tray.append(self.make_chip(name))
        self.tray_label.set_visible(bool(pending))

    # ---- apply -------------------------------------------------------------
    def on_apply(self, _btn):
        pending = self.unseated()
        if pending:
            self.status.set_label(f"seat these machines first: {', '.join(pending)}")
            return
        by_name = {m["name"]: m for m in self.machines}
        for side, name in self.seats.items():
            if name:
                by_name[name]["role"] = side
        self.options.setdefault("extend", {})
        for side, combo in self.res_entries.items():
            mode = combo.get_child().get_text().strip()
            if mode:
                self.options["extend"][side] = mode
        cfg = {"machines": self.machines, "options": self.options}
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
        try:
            with open(FLEET_DROP, "w") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass
        self.status.set_label("applying…")

        def worker():
            proc = subprocess.run([os.path.expanduser("~/.local/bin/multibox"), "apply"],
                                  capture_output=True, text=True)
            msg = ("✓ applied — run 'multibox apply --from-drop' on the other machines"
                   if proc.returncode == 0
                   else "✗ " + (proc.stderr.strip() or "apply failed"))
            GLib.idle_add(self.status.set_label, msg)

        import threading
        threading.Thread(target=worker, daemon=True).start()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.multibox.Setup",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window() or SetupWindow(self)
        win.present()


if __name__ == "__main__":
    App().run()
