#!/usr/bin/env python3
"""multibox-setup — arrange the fleet's seats and options, then apply.

A picture of the desk drawn with monitor shapes: drag a laptop's screen into
the LEFT or RIGHT seat beside the (fixed) desktop, tune options, Apply.
Applying rewrites ~/.config/multibox/machines.json, regenerates this
machine's configs via `multibox apply`, and saves the fleet description to
~/Drop/multibox-fleet.json so other machines can adopt it with
`multibox apply --from-drop`.
"""
import json
import os
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, GLib, Gio, GObject, Adw  # noqa: E402

CONFIG = os.path.expanduser("~/.config/multibox/machines.json")
FLEET_DROP = os.path.expanduser("~/Drop/multibox-fleet.json")
MULTIBOX = os.path.expanduser("~/.local/bin/multibox")
RESOLUTIONS = ["1920x1080@60", "1600x900@60", "2560x1440@60", "1280x800@60"]

CSS = b"""
.desk { padding: 18px 6px; }
.screen {
  background: linear-gradient(160deg, alpha(@accent_bg_color, 0.45), shade(@window_bg_color, 0.55));
  border: 2px solid alpha(currentColor, 0.55);
  border-radius: 10px;
  padding: 6px;
}
.screen.desktop-screen {
  background: linear-gradient(160deg, alpha(@accent_bg_color, 0.65), shade(@window_bg_color, 0.5));
}
.screen.offline { opacity: 0.55; }
.screen-name { font-weight: 800; font-size: 1.05em; }
.screen-ip { opacity: 0.65; font-size: 0.8em; }
.stand { background: alpha(currentColor, 0.45); border-radius: 0 0 4px 4px; }
.base  { background: alpha(currentColor, 0.45); border-radius: 3px; }
.seat-slot {
  border: 2px dashed alpha(currentColor, 0.3);
  border-radius: 14px;
  padding: 8px;
  min-width: 172px; min-height: 128px;
}
.seat-slot.hover { border-color: @accent_bg_color; background: alpha(@accent_bg_color, 0.12); }
.seat-caption { opacity: 0.55; font-size: 0.75em; font-weight: 700; letter-spacing: 0.12em; }
.dot-on  { color: #33d17a; }
.dot-off { color: #e01b24; }
.dot-unknown { color: alpha(currentColor, 0.4); }
"""


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


def local_monitors():
    """The physical monitors of the machine running this window, left to
    right, so the desk picture shows the real hardware."""
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"],
                             capture_output=True, text=True, timeout=3)
        mons = json.loads(out.stdout)
        return sorted((m for m in mons if not m["name"].startswith("HEADLESS")),
                      key=lambda m: m["x"])
    except Exception:
        return []


def my_tailscale_ip():
    try:
        return subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


class SetupWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Multibox Setup")
        self.set_default_size(720, -1)

        cfg = load_config()
        self.machines = cfg["machines"]
        self.options = cfg.get("options", {})
        self.online = {}  # name -> True/False (None while unknown)
        self.desktop = next((m for m in self.machines if m["role"] == "desktop"), None)
        if self.desktop is None:
            self.machines[0]["role"] = "desktop"
            self.desktop = self.machines[0]
        self.seats = {"left": None, "right": None}
        for m in self.machines:
            if m["role"] in self.seats and self.seats[m["role"]] is None:
                self.seats[m["role"]] = m["name"]

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        view = Adw.ToolbarView()
        self.toasts.set_child(view)

        header = Adw.HeaderBar()
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        header.pack_end(apply_btn)
        view.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_top=8, margin_bottom=18, margin_start=18, margin_end=18)
        view.set_content(outer)

        title = Gtk.Label(label="Drag each laptop's screen into the seat where it sits on your desk")
        title.add_css_class("dim-label")
        outer.append(title)

        # ---- the desk -------------------------------------------------------
        desk = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        desk.add_css_class("desk")
        outer.append(desk)
        self.slot_boxes = {}
        desk.append(self.make_slot("left"))
        desk.append(self.desktop_widget())
        desk.append(self.make_slot("right"))

        # ---- unseated machines ---------------------------------------------
        self.tray_label = Gtk.Label(label="Unseated — drag into a seat:", xalign=0)
        self.tray_label.add_css_class("dim-label")
        outer.append(self.tray_label)
        self.tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tray_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        tray_target.connect("drop", lambda t, name, x, y: self.seat(name, None))
        self.tray.add_controller(tray_target)
        outer.append(self.tray)

        # ---- options --------------------------------------------------------
        group = Adw.PreferencesGroup(title="Extra-monitor (extend-display)")
        outer.append(group)
        self.res_entries = {}
        for side in ("left", "right"):
            row = Adw.EntryRow(title=f"{side.capitalize()} resolution")
            row.set_text(self.options.get("extend", {}).get(side, RESOLUTIONS[0]))
            menu = Gtk.MenuButton(icon_name="pan-down-symbolic", valign=Gtk.Align.CENTER)
            menu.add_css_class("flat")
            pop = Gtk.Popover()
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for r in RESOLUTIONS:
                b = Gtk.Button(label=r)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, row=row, r=r, pop=pop:
                          (row.set_text(r), pop.popdown()))
                pop_box.append(b)
            pop.set_child(pop_box)
            menu.set_popover(pop)
            row.add_suffix(menu)
            group.add(row)
            self.res_entries[side] = row

        self.fps_row = Adw.SpinRow(
            title="Stream frame rate",
            subtitle="Higher is smoother; lower costs less CPU",
            adjustment=Gtk.Adjustment(lower=24, upper=120, step_increment=6, page_increment=30))
        self.fps_row.set_value(int(self.options.get("extend", {}).get("fps", 60)))
        group.add(self.fps_row)

        self.h264_row = Adw.SwitchRow(
            title="H.264 video streaming",
            subtitle="GPU-encoded video instead of VNC tiles — much smoother motion "
                     "(needs the wlvncc viewer on the laptops; falls back to VNC without it)")
        self.h264_row.set_active(bool(self.options.get("extend", {}).get("h264", False)))
        group.add(self.h264_row)

        self.input_row = Adw.SwitchRow(
            title="Allow input from laptop screens",
            subtitle="Lets the VNC viewer send clicks/keys back (off = view-only, safer)")
        self.input_row.set_active(bool(self.options.get("extend", {}).get("input", False)))
        group.add(self.input_row)

        hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, label=(
            "Applying updates this machine and saves the fleet file to ~/Drop — "
            "run “multibox apply --from-drop” on the others."))
        hint.add_css_class("dim-label")
        outer.append(hint)

        self.render()
        self.refresh_online()

    # ---- widgets -----------------------------------------------------------
    def desktop_widget(self):
        """The desktop drawn as its real monitor row when this window runs on
        the desktop itself; a single generic screen elsewhere."""
        monitors = local_monitors() if my_tailscale_ip() == self.desktop["ip"] else []
        if not monitors:
            return self.machine_widget(self.desktop["name"], desktop=True)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      halign=Gtk.Align.CENTER)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        for m in monitors:
            shape = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                            valign=Gtk.Align.CENTER)
            shape.add_css_class("screen")
            shape.add_css_class("desktop-screen")
            # width scaled from the mode so a 4K next to a 1080p reads true
            w = max(84, min(150, int(m["width"] / 22)))
            shape.set_size_request(w, int(w * 0.6))
            mname = Gtk.Label(label=m["name"])
            mname.add_css_class("screen-name")
            shape.append(mname)
            res = Gtk.Label(label=f'{m["width"]}×{m["height"]}')
            res.add_css_class("screen-ip")
            shape.append(res)
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             halign=Gtk.Align.CENTER)
            holder.append(shape)
            base = Gtk.Box(halign=Gtk.Align.CENTER)
            base.add_css_class("stand")
            base.set_size_request(18, 10)
            holder.append(base)
            row.append(holder)
        col.append(row)
        caption = Gtk.Label(
            label=f'{self.desktop["name"]} · {len(monitors)} monitors · {self.desktop["ip"]}')
        caption.add_css_class("screen-ip")
        col.append(caption)
        return col

    def machine_widget(self, name, desktop=False, draggable=False):
        """A little monitor: screen with name/IP/status dot, plus a stand."""
        machine = next(m for m in self.machines if m["name"] == name)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      halign=Gtk.Align.CENTER)
        screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         valign=Gtk.Align.CENTER)
        screen.add_css_class("screen")
        if desktop:
            screen.add_css_class("desktop-screen")
        screen.set_size_request(200 if desktop else 150, 122 if desktop else 92)

        state = self.online.get(name)
        dot = Gtk.Label(label="●")
        dot.add_css_class("dot-on" if state else
                          ("dot-unknown" if state is None else "dot-off"))
        if state is False:
            screen.add_css_class("offline")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      halign=Gtk.Align.CENTER)
        label = Gtk.Label(label=name)
        label.add_css_class("screen-name")
        top.append(dot)
        top.append(label)
        screen.append(top)
        ip = Gtk.Label(label=machine["ip"])
        ip.add_css_class("screen-ip")
        screen.append(ip)
        if desktop:
            role = Gtk.Label(label="this desk's anchor")
            role.add_css_class("screen-ip")
            screen.append(role)
        col.append(screen)

        if desktop:
            stand = Gtk.Box(halign=Gtk.Align.CENTER)
            stand.add_css_class("stand")
            stand.set_size_request(26, 16)
            col.append(stand)
            base = Gtk.Box(halign=Gtk.Align.CENTER)
            base.add_css_class("base")
            base.set_size_request(84, 5)
            col.append(base)
        else:
            base = Gtk.Box(halign=Gtk.Align.CENTER)
            base.add_css_class("base")
            base.set_size_request(150, 6)
            col.append(base)

        if draggable:
            src = Gtk.DragSource()
            src.set_actions(Gdk.DragAction.MOVE)
            src.connect("prepare", lambda s, x, y:
                        Gdk.ContentProvider.new_for_value(name))
            col.add_controller(src)
        return col

    def make_slot(self, side):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        valign=Gtk.Align.END)
        frame.add_css_class("seat-slot")
        cap = Gtk.Label(label=f"{side.upper()} SEAT")
        cap.add_css_class("seat-caption")
        frame.append(cap)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         valign=Gtk.Align.END, vexpand=True)
        frame.append(holder)
        self.slot_boxes[side] = holder

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", lambda t, name, x, y, s=side: self.seat(name, s))
        target.connect("enter", lambda t, x, y, f=frame:
                       (f.add_css_class("hover"), Gdk.DragAction.MOVE)[1])
        target.connect("leave", lambda t, f=frame: f.remove_css_class("hover"))
        frame.add_controller(target)
        return frame

    # ---- state -------------------------------------------------------------
    def seat(self, name, side):
        if name == self.desktop["name"]:
            return False
        for s in self.seats:
            if self.seats[s] == name:
                self.seats[s] = None
        if side is not None:
            self.seats[side] = name
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
                holder.append(self.machine_widget(self.seats[side], draggable=True))
            else:
                empty = Gtk.Label(label="(empty seat)")
                empty.add_css_class("dim-label")
                empty.set_margin_bottom(34)
                holder.append(empty)
        while (c := self.tray.get_first_child()) is not None:
            self.tray.remove(c)
        pending = self.unseated()
        for name in pending:
            self.tray.append(self.machine_widget(name, draggable=True))
        self.tray_label.set_visible(bool(pending))
        self.tray.set_visible(bool(pending))

    def refresh_online(self):
        def worker():
            try:
                out = subprocess.run([MULTIBOX, "status", "--json"],
                                     capture_output=True, text=True, timeout=10)
                peers = json.loads(out.stdout).get("peers", [])
                status = {p["name"]: bool(p["up"]) for p in peers}
                status[self.desktop["name"]] = True
            except Exception:
                status = {}
            GLib.idle_add(self.apply_online, status)
        threading.Thread(target=worker, daemon=True).start()

    def apply_online(self, status):
        self.online = status
        self.render()

    # ---- apply -------------------------------------------------------------
    def on_apply(self, _btn):
        pending = self.unseated()
        if pending:
            self.toasts.add_toast(Adw.Toast(
                title=f"Seat these machines first: {', '.join(pending)}"))
            return
        by_name = {m["name"]: m for m in self.machines}
        for side, name in self.seats.items():
            if name:
                by_name[name]["role"] = side
        extend = self.options.setdefault("extend", {})
        for side, row in self.res_entries.items():
            mode = row.get_text().strip()
            if mode:
                extend[side] = mode
        extend["input"] = self.input_row.get_active()
        extend["fps"] = int(self.fps_row.get_value())
        extend["h264"] = self.h264_row.get_active()
        cfg = {"machines": self.machines, "options": self.options}
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
        try:
            with open(FLEET_DROP, "w") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass
        self.toasts.add_toast(Adw.Toast(title="Applying…"))

        def worker():
            proc = subprocess.run([MULTIBOX, "apply"], capture_output=True, text=True)
            msg = ("Applied — run “multibox apply --from-drop” on the other machines"
                   if proc.returncode == 0
                   else "Apply failed: " + (proc.stderr.strip()[:120] or "unknown error"))
            GLib.idle_add(lambda: self.toasts.add_toast(Adw.Toast(title=msg)))
            GLib.idle_add(self.refresh_online)

        threading.Thread(target=worker, daemon=True).start()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.multibox.Setup",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window() or SetupWindow(self)
        win.present()


if __name__ == "__main__":
    App().run()
