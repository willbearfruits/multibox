# multibox

One desk, several [Omarchy](https://omarchy.org/) machines working as one —
a desktop plus laptops seated beside it, glued together over a
[Tailscale](https://tailscale.com/) tailnet. Everything is encrypted,
works away from home, and nothing is exposed on the LAN.

| Capability | How |
|---|---|
| One mouse + keyboard for every machine (edge switching) | [lan-mouse](https://github.com/feschber/lan-mouse), configured per seat position |
| Clipboard sync — text and screenshots | **clipsync** (ours, ~200 lines of Python, zero deps) |
| `~/Drop` folder mirrored everywhere | [syncthing](https://syncthing.net/) |
| Drag a file onto a machine's name to send it | **dropzone** (GTK4) → clipsync's receiver |
| Laptops as extra monitors, with window dragging | **extend-display**: headless Hyprland output + wayvnc |
| Bar widget: fleet health + quick actions | Omarchy shell plugin (this repo) |

## Requirements

- 2+ machines running Omarchy (or any Arch + Hyprland setup)
- All of them on the same tailnet (`tailscale up`)

## Install

On **every** machine:

```bash
git clone https://github.com/willbearfruits/multibox
cd multibox && ./install.sh
```

The installer asks once for the machine list — name, tailscale IP, and role
(`desktop`, or `left`/`right` for a laptop's seat relative to the desktop) —
and stores it in `~/.config/multibox/machines.json`. Copy that file to
`machines.json` in the repo checkout on the other machines (or answer the
prompts again) so every machine agrees on the fleet.

Then run `multibox pair` on **each** machine. It exchanges the lan-mouse TLS
fingerprints and syncthing device IDs that the installers dropped into
`~/Drop/multibox-pairing/` (delivered over the tailnet where possible; the
fallback is copying those small JSON files between machines by any means).
Pairing is done when `multibox status` shows every peer online.

## Daily use

- Shove the cursor off a screen edge → you're typing on the machine seated
  there. `LCtrl+LShift+LAlt+RCtrl` releases a stuck cursor.
- Copy anywhere, paste anywhere. `multibox pause` before copying secrets you
  want to keep on one machine (`multibox resume` after).
- Drop a file in `~/Drop` → it's on every machine.
- Drag files onto a peer's name in **Dropzone** → they land in that machine's
  `~/Drop/incoming` with a notification.
- Extra monitor: `multibox extend start left` on the desktop, then launch
  **Desktop Monitor** on the left-seated laptop (fullscreen VNC of a real
  Hyprland output — dragging windows past that edge moves them "onto the
  laptop").
- `multibox status` — who's online, what's running.
- `multibox setup` (or the **Multibox Setup** launcher app, or **Setup…** in
  the bar widget) — a window where you drag machines into the seats matching
  your desk and set per-side extra-monitor resolutions. Applying updates the
  local machine and saves the fleet file to `~/Drop`; run
  `multibox apply --from-drop` on the others to adopt it.

## Moving windows between machines

A window can't truly migrate between computers (its process lives on one
machine), but extend-display gets you the working equivalent: the laptop
shows a **real Hyprland output** of the desktop, so desktop windows move
onto the laptop's screen by dragging them past that edge — or instantly via
`multibox send-window left|right` (also a button in the bar widget while
extend is active). While a side is an extra monitor, lan-mouse's edge for
that side is paused automatically (the same edge can't both switch input and
host a screen) and resumes on stop. The VNC stream is **view-only** unless
you enable "Allow input from laptop screens" in Setup.

## Omarchy bar widget

The installer adds a bar widget on Omarchy machines: an icon that dims (or
turns urgent) as peers drop offline, and a popup with per-machine status,
a clipboard-sync toggle, extend-display + send-window controls (desktop),
and Dropzone/Setup launchers. **Drag files onto the widget**: dragging over
the bar icon opens the popup, and dropping files on a machine's row sends
them to that machine's `~/Drop/incoming` (result arrives as a notification).
`multibox send <machine> <files...>` does the same from a terminal.

To install **just the widget** on a machine set up by other means:

```bash
omarchy plugin add https://github.com/willbearfruits/multibox --enable
```

## How clipsync works

One small Python daemon per machine. `wl-paste --watch` pushes each local
clipboard change to every peer's HTTP endpoint (`:46264/clip`, tailnet IPs
only); receivers `wl-copy` it locally. Loop prevention: the receiver records
the content hash *before* setting the clipboard, so the watch event a remote
set triggers is recognised and not re-broadcast. The same daemon receives
dropzone's file uploads on `/file` (streamed, up to 8 GB).

Security model: every service binds **only** to the machine's tailscale IP,
and clipsync rejects requests from IPs outside the fleet. Transport
encryption and device identity come from the tailnet; nothing listens on
the LAN.

## Repo layout

```
install.sh            interactive installer (run on every machine)
bin/multibox          fleet CLI: status / setup / apply / pause / resume / drop / extend / pair
setup/multibox-setup.py  GTK4 seat-arrangement + options window
bin/extend-display    headless Hyprland output + wayvnc (desktop only)
clipsync/clipsync.py  clipboard sync daemon + file receiver
dropzone/dropzone.py  GTK4 drag-and-drop sender
manifest.json         Omarchy shell plugin manifest (repo doubles as the plugin)
plugin/BarWidget.qml  the bar widget
machines.example.json fleet description template (machines.json is gitignored)
```
