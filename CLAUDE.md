# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Public repo for running several Omarchy (Arch/Hyprland) machines as one desk over a Tailscale tailnet: shared mouse/keyboard (lan-mouse), clipboard sync (clipsync, ours), `~/Drop` file sync (syncthing), drag-and-drop file sending (dropzone), laptops-as-extra-monitors (extend-display), and an Omarchy bar widget. No build system, no tests — Python, bash, QML, and generated configs.

The maintainer's fleet specifics live in `CLAUDE.local.md` and `machines.json` — both gitignored. Never commit real tailnet IPs, fingerprints, device IDs, or usernames; `machines.example.json` shows the shape.

## Architecture

- **`~/.config/multibox/machines.json`** is the single source of truth: `{"machines": [{name, ip, role}], "options": {extend: {left, right, fps, input}}}` with exactly one `desktop`; laptop roles (`left`/`right`) are seat positions relative to the desktop. **`multibox apply`** generates every per-tool config from it (clipsync config, lan-mouse `[[clients]]`, the laptop's tuned `desktop-monitor` viewer) and restarts services — `install.sh` and the setup window both delegate to it. Fleet changes propagate via `~/Drop/multibox-fleet.json` + `multibox apply --from-drop` on each other machine. Machines identify themselves by matching `tailscale ip -4` against the list.
- **clipsync** (`clipsync/clipsync.py`) is the core service and shared transport: HTTP server bound *only* to the machine's tailscale IP on port 46264 (`POST /clip` clipboard, `POST /file` streamed file receive into `~/Drop/incoming`), plus a `wl-paste --watch` child that re-invokes the script as `clipsync push`. **Loop prevention**: the content hash is written to `$XDG_RUNTIME_DIR/clipsync.last` *before* `wl-copy`, so the watch event a remote set triggers is skipped — keep this ordering. Push writes the hash only after ≥1 peer accepted (failed pushes retry on the next copy); the daemon restarts a dead watcher. `GET /ping` is the health probe; `POST /monitor` (fixed start/stop actions only, never arbitrary commands) drives the extra-monitor viewer. **dropzone** posts to peers' `/file`; it has no config of its own — and so do the bar widget's drop targets, via `multibox send`.
- **`bin/multibox`** is the fleet CLI (status/setup/apply/send/send-window/pause/resume/doctor/drop/extend/pair/pairing-info). `pause` touches `$XDG_RUNTIME_DIR/clipsync.paused` rather than stopping the service — the clipsync daemon keeps serving `/file` and `/monitor` while paused (push no-ops; `/clip` answers 503 so senders retry later). The Omarchy bar widget (`plugin/BarWidget.qml`) is a thin view over `multibox status --json` and shells out to the same CLI for actions; the GTK4/libadwaita setup window (`setup/multibox-setup.py`) edits machines.json and runs `multibox apply`.
- **Extra-monitor pipeline** (`multibox extend start <side>`, desktop only): headless Hyprland output positioned past that edge → `wayvnc -g -f <fps>` on port 5901/5902 (GPU capture with software fallback, view-only unless `options.extend.input`) → a `POST /monitor {"action":"start"}` to that side's laptop makes its clipsync daemon launch the fullscreen `desktop-monitor` viewer (Tight encoding, low compression — pinned; TigerVNC's auto-negotiation is laggier). The viewer runs as its own transient unit (`systemd-run --user --unit desktop-monitor`), NOT as a clipsync child — restarting clipsync kills its whole cgroup, and the viewer must survive that. `multibox extend start <side>` on an already-extended side just re-triggers the viewer, which is how you relaunch a closed one. While a side is extended its lan-mouse client is paused (same edge, two owners) and resumed on stop. `multibox send-window <side>` = `hyprctl dispatch movewindow mon:<headless>` from the extend state file. Windows can only be *displayed* remotely, never migrated — don't promise laptop→desktop window moves.
- **Plugin**: the repo root doubles as an Omarchy shell plugin (`manifest.json` at root, entry point `plugin/BarWidget.qml`) so `omarchy plugin add <git-url>` works directly. Validate with `omarchy plugin validate .`. QML uses the shell's `qs.Ui` components (BarWidget, BarIconButton, PopupCard, ToggleSwitch…) — read `/usr/share/omarchy/shell/Ui/` for their APIs.
- **Pairing** is two-phase: `install.sh` on each machine writes its lan-mouse fingerprint + syncthing ID to `~/Drop/multibox-pairing/<name>.json` (and best-effort taildrops it to peers); `multibox pair` on each machine applies peers' files (appends to lan-mouse `[authorized_fingerprints]`, adds syncthing devices, shares the `drop` folder).

### Security invariants

Everything binds only to the tailscale IP (never 0.0.0.0); clipsync rejects requests from non-fleet IPs (GET /ping and POSTs alike); extend-display refuses to start when tailscale is down and runs wayvnc `--disable-input` unless `options.extend.input` is true. extend-display also pauses the lan-mouse client for its side while active (same edge, two owners) and resumes it on stop. Encryption and device auth come from the tailnet — preserve this in any change.

### lan-mouse config format

lan-mouse ≥ 0.11 requires `[[clients]]` array entries with `position` and `activate_on_startup = true`. The old `[left]`/`[right]` section format is **silently ignored** (daemon runs with zero clients — the failure mode is "mouse doesn't cross edges" with clean logs; check `lan-mouse cli list`). `multibox apply` preserves the existing `[authorized_fingerprints]` across regenerations.

## Deployment model — edits here are not live

`install.sh` **copies** files to `~/.local/bin/` and `~/.config/omarchy/plugins/multibox/`; nothing runs from the checkout. After editing, re-run `./install.sh` (idempotent, regenerates configs, restarts services) or copy the file and `systemctl --user restart clipsync` / `omarchy-shell shell rescanPlugins` as appropriate.

## Ops / debugging

```bash
multibox status                          # fleet health (peer probes run in parallel)
multibox doctor                          # deeper check: config, services, pairing, peers
systemctl --user status clipsync lan-mouse syncthing
journalctl --user -u clipsync -f         # received clips, push failures
lan-mouse cli list                       # MUST show clients with active: true
                                         # (a side paused by extend is active: false by design)
multibox extend status                   # desktop only
omarchy plugin validate .                # after touching manifest.json / QML
```

Remote-access notes for the maintainer's own machines are in `CLAUDE.local.md`.
