#!/usr/bin/env python3
"""dropzone — drag files onto a peer's name to send them to that machine.

Files arrive on the peer at ~/Drop/incoming (received by the clipsync
daemon's /file endpoint, tailnet-only). One row per peer from
~/.config/clipsync/config.json's "names" map.
"""
import json
import os
import threading
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio  # noqa: E402

PORT = 46264
CONFIG = os.path.expanduser("~/.config/clipsync/config.json")


def load_peers():
    with open(CONFIG) as f:
        cfg = json.load(f)
    names = cfg.get("names", {})
    return [(names.get(ip, ip), ip) for ip in cfg["peers"]]


def send_file(ip, path, done):
    size = os.path.getsize(path)
    req = urllib.request.Request(
        f"http://{ip}:{PORT}/file",
        data=open(path, "rb"),
        headers={
            "X-Filename": os.path.basename(path),
            "Content-Length": str(size),
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=600)
        done(True, os.path.basename(path))
    except OSError as e:
        done(False, f"{os.path.basename(path)}: {e}")


class Dropzone(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Dropzone")
        self.set_default_size(260, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(box)
        for name, ip in load_peers():
            box.append(self.peer_row(name, ip))

    def peer_row(self, name, ip):
        frame = Gtk.Frame()
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=14, margin_bottom=14, margin_start=10, margin_end=10)
        title = Gtk.Label(label=f"→ {name}")
        title.add_css_class("title-3")
        hint = Gtk.Label(label="drop files here")
        hint.add_css_class("dim-label")
        statuses = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.append(title)
        row.append(hint)
        row.append(statuses)
        frame.set_child(row)

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

        def on_drop(_t, filelist, _x, _y):
            files = [f.get_path() for f in filelist.get_files() if f.get_path()]
            if not files:
                return False
            # one status line per file, so a failure can't be papered over
            # by a later success finishing after it
            for path in files:
                label = Gtk.Label(label=f"… {os.path.basename(path)}")
                label.add_css_class("dim-label")
                while len(list(statuses)) >= 5:
                    statuses.remove(statuses.get_first_child())
                statuses.append(label)

                def done(ok, msg, label=label):
                    GLib.idle_add(label.set_label, ("✓ " if ok else "✗ ") + msg)

                threading.Thread(target=send_file, args=(ip, path, done),
                                 daemon=True).start()
            return True

        target.connect("drop", on_drop)
        frame.add_controller(target)
        return frame


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.multibox.Dropzone",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window() or Dropzone(self)
        win.present()


if __name__ == "__main__":
    App().run()
