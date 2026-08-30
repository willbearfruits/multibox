#!/usr/bin/env python3
"""clipsync — clipboard sync between machines over the tailnet.

Architecture (one instance per machine):
  daemon  — HTTP server on the tailscale IP receiving clips from peers,
            plus a `wl-paste --watch` child that invokes `clipsync push`
            on every local clipboard change.
  push    — read the local clipboard (image/png preferred, else text),
            dedup against the last seen hash, POST to every peer.

Loop prevention: whichever side sets the clipboard records its content hash
in a state file; the watch event that set triggers is then skipped.

Security: binds only to this machine's tailscale IP and accepts POSTs only
from configured peer IPs. The tailnet provides encryption + device auth.
"""
import base64
import hashlib
import http.server
import json
import os
import subprocess
import sys
import time
import urllib.request

CONFIG = os.path.expanduser("~/.config/clipsync/config.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
STATE = os.path.join(RUNTIME, "clipsync.last")
# `multibox pause` touches this: clipboard stops syncing, but /file and
# /monitor stay up (pausing must not break file drops or extend-display)
PAUSED = os.path.join(RUNTIME, "clipsync.paused")
PORT = 46264
MAX_BYTES = 32 * 1024 * 1024  # refuse clips larger than 32 MB
TEXT_MIMES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING")


def load_config():
    with open(CONFIG) as f:
        cfg = json.load(f)
    return cfg["listen_ip"], cfg["peers"], cfg.get("names") or {}


def read_state():
    try:
        with open(STATE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def write_state(digest):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        f.write(digest)
    os.replace(tmp, STATE)


def clip_digest(mime, data):
    return hashlib.sha256(mime.encode() + b"\0" + data).hexdigest()


def read_clipboard():
    """Return (mime, bytes) for the current clipboard, or (None, None)."""
    try:
        types = subprocess.run(
            ["wl-paste", "--list-types"], capture_output=True, timeout=5
        ).stdout.decode(errors="replace").split()
        mime = None
        if "image/png" in types:
            mime = "image/png"
        else:
            for t in TEXT_MIMES:
                if t in types:
                    mime = "text/plain;charset=utf-8"
                    break
        if mime is None:
            return None, None
        proc = subprocess.run(
            ["wl-paste", "--no-newline", "--type", mime], capture_output=True, timeout=10
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, None
    if proc.returncode != 0 or not proc.stdout:
        return None, None
    return mime, proc.stdout


def set_clipboard(mime, data):
    subprocess.run(["wl-copy", "--type", mime], input=data, timeout=10)


def push():
    if os.path.exists(PAUSED):
        return
    _, peers, _ = load_config()
    mime, data = read_clipboard()
    if mime is None or len(data) > MAX_BYTES:
        return
    digest = clip_digest(mime, data)
    if digest == read_state():
        return  # our own set, or already synced
    body = json.dumps(
        {"mime": mime, "data": base64.b64encode(data).decode()}
    ).encode()
    delivered = 0
    for peer in peers:
        req = urllib.request.Request(
            f"http://{peer}:{PORT}/clip", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            delivered += 1
        except OSError as e:
            print(f"push to {peer} failed: {e}", flush=True)
    # Recorded only once someone has the clip: a totally failed push leaves
    # the state alone, so copying the same thing again retries the delivery.
    if delivered:
        write_state(digest)


INCOMING = os.path.expanduser("~/Drop/incoming")
MAX_FILE = 8 * 1024 * 1024 * 1024  # 8 GB


def unique_path(directory, name):
    name = os.path.basename(name)[:200]
    name = "".join(c if c.isprintable() else "_" for c in name)
    if name in ("", ".", ".."):
        name = "unnamed"
    path = os.path.join(directory, name)
    stem, ext = os.path.splitext(name)
    n = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem} ({n}){ext}")
        n += 1
    return path


class Handler(http.server.BaseHTTPRequestHandler):
    peers = []
    names = {}
    timeout = 30  # per-recv socket timeout: a stalled peer can't pin a thread

    def body_length(self, cap):
        """Content-Length as an int in (0, cap], else None (caller rejects)."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return None
        return length if 0 < length <= cap else None

    def do_GET(self):
        """Health probe: `multibox status` checks /ping instead of a bare
        TCP connect, so 'online' means the daemon actually answers."""
        if self.client_address[0] not in self.peers:
            self.send_error(403)
            return
        if self.path != "/ping":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        if self.client_address[0] not in self.peers:
            self.send_error(403)
            return
        if self.path == "/file":
            self.recv_file()
            return
        if self.path == "/monitor":
            self.monitor_ctl()
            return
        if self.path != "/clip":
            self.send_error(404)
            return
        if os.path.exists(PAUSED):
            # non-2xx so the sender counts it as undelivered and retries on
            # the next copy instead of considering the clip synced
            self.send_error(503, "clipboard sync paused here")
            return
        length = self.body_length(MAX_BYTES * 2)
        if length is None:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            mime = payload["mime"]
            data = base64.b64decode(payload["data"])
        except (ValueError, KeyError):
            self.send_error(400)
            return
        if mime != "image/png" and not mime.startswith("text/plain"):
            self.send_error(415)
            return
        # record BEFORE setting so the watch event this triggers is skipped
        write_state(clip_digest(mime, data))
        set_clipboard(mime, data)
        self.send_response(204)
        self.end_headers()
        print(f"received {mime} ({len(data)} B) from {self.client_address[0]}", flush=True)

    def monitor_ctl(self):
        """Desktop-triggered extra-monitor viewer control. Fixed actions only
        (never arbitrary commands): 'start' launches the fullscreen VNC viewer
        of the desktop's headless output, 'stop' closes it. This is what makes
        toggling extend on the desktop light up the laptop by itself."""
        length = self.body_length(4096)
        if length is None:
            self.send_error(400)
            return
        try:
            action = json.loads(self.rfile.read(length))["action"]
        except (ValueError, KeyError):
            self.send_error(400)
            return
        viewer = os.path.expanduser("~/.local/bin/desktop-monitor")
        if action == "start" and os.path.exists(viewer):
            if subprocess.run(["pgrep", "-f", "vncviewer -FullScreen"],
                              capture_output=True).returncode != 0:
                # launch in its own transient unit: the viewer must survive
                # clipsync restarts (setsid detaches the session but stays in
                # this service's cgroup, which systemd kills wholesale)
                if subprocess.run(
                        ["systemd-run", "--user", "--collect",
                         "--unit", "desktop-monitor", viewer],
                        capture_output=True).returncode != 0:
                    subprocess.Popen(["setsid", "-f", viewer],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        elif action == "stop":
            subprocess.run(["systemctl", "--user", "stop", "desktop-monitor.service"],
                           capture_output=True)
            subprocess.run(["pkill", "-f", "vncviewer -FullScreen"],
                           capture_output=True)
        else:
            self.send_error(400)
            return
        self.send_response(204)
        self.end_headers()
        print(f"monitor viewer: {action} (from {self.client_address[0]})", flush=True)

    def recv_file(self):
        """Raw-body file upload: X-Filename header + streamed bytes."""
        name = self.headers.get("X-Filename", "")
        length = self.body_length(MAX_FILE)
        if not name or length is None:
            self.send_error(400)
            return
        os.makedirs(INCOMING, exist_ok=True)
        dest = unique_path(INCOMING, name)
        tmp = dest + ".part"
        remaining = length
        try:
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("short read")
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, dest)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            print(f"file receive failed: {e}", flush=True)
            self.send_error(500)
            return
        self.send_response(204)
        self.end_headers()
        print(f"received file {os.path.basename(dest)} ({length} B)", flush=True)
        sender = self.names.get(self.client_address[0], self.client_address[0])
        subprocess.Popen(["notify-send", "-a", "Dropzone",
                          f"File from {sender}",
                          f"{os.path.basename(dest)} → ~/Drop/incoming"])

    def log_message(self, *_):
        pass


def start_watcher():
    return subprocess.Popen(
        ["wl-paste", "--watch", sys.executable, os.path.abspath(__file__), "push"]
    )


def daemon():
    listen_ip, peers, names = load_config()
    Handler.peers = peers
    Handler.names = names
    watcher = start_watcher()

    def supervise():
        """Local copies silently stop syncing if wl-paste dies; restart it."""
        nonlocal watcher
        while True:
            time.sleep(10)
            if watcher.poll() is not None:
                print("wl-paste watcher died, restarting", flush=True)
                watcher = start_watcher()

    import threading
    threading.Thread(target=supervise, daemon=True).start()
    print(f"clipsync: listening on {listen_ip}:{PORT}, peers: {peers}", flush=True)
    try:
        server = http.server.ThreadingHTTPServer((listen_ip, PORT), Handler)
        server.serve_forever()
    finally:
        watcher.terminate()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if cmd == "push":
        push()
    elif cmd == "daemon":
        while True:  # survive tailscale IP not being up yet at login
            try:
                daemon()
            except (OSError, ValueError, KeyError) as e:
                # OSError: bind failed (tailscale not up yet, port busy)
                # ValueError/KeyError: broken config — fixable by 'multibox apply'
                print(f"daemon start failed ({e}), retrying in 5s", flush=True)
                time.sleep(5)
