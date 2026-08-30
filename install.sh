#!/bin/bash
# multibox installer — run on EVERY machine in the fleet (desktop and laptops).
#
#   git clone https://github.com/willbearfruits/multibox && cd multibox && ./install.sh
#
# Asks for the machine list once (or reuses ~/.config/multibox/machines.json),
# then sets up everything this machine's role needs: clipsync (clipboard sync
# + file receiver), lan-mouse (shared mouse/keyboard), syncthing (~/Drop),
# dropzone, extend-display (desktop), the Desktop Monitor viewer (laptops),
# and the Omarchy bar widget when running on Omarchy.
#
# After installing on all machines, run `multibox pair` on each to exchange
# lan-mouse fingerprints and syncthing device IDs.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/multibox"
CONFIG="$CONFIG_DIR/machines.json"
CLIPSYNC_PORT=46264

die() { echo "install: $*" >&2; exit 1; }

command -v tailscale >/dev/null || die "tailscale is required (all traffic runs over the tailnet)"
MY_IP=$(tailscale ip -4 2>/dev/null) || die "tailscale is not up — run 'sudo tailscale up' first"
command -v jq >/dev/null || die "jq is required"

# ---------- machine list ----------------------------------------------------
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG" ] && [ -f "$DIR/machines.json" ]; then
  cp "$DIR/machines.json" "$CONFIG"
fi
if [ -f "$CONFIG" ]; then
  echo "Using existing machine list ($CONFIG):"
  jq -r '.machines[] | "  \(.name)  \(.ip)  \(.role)"' "$CONFIG"
else
  echo "Describe every machine in the fleet (exactly one 'desktop';"
  echo "laptop roles are their position relative to the desktop: left/right)."
  echo "This machine's tailscale IP is $MY_IP. Empty name finishes."
  machines="[]"
  while true; do
    read -rp "machine name (hostname): " name
    [ -z "$name" ] && break
    read -rp "  tailscale IP: " ip
    read -rp "  role (desktop/left/right): " role
    case "$role" in desktop|left|right) ;; *) echo "  bad role, try again"; continue ;; esac
    machines=$(jq --arg n "$name" --arg i "$ip" --arg r "$role" \
      '. + [{name: $n, ip: $i, role: $r}]' <<<"$machines")
  done
  jq -n --argjson m "$machines" '{machines: $m}' > "$CONFIG"
fi

[ "$(jq '[.machines[] | select(.role == "desktop")] | length' "$CONFIG")" = 1 ] \
  || die "the machine list must contain exactly one 'desktop'"
SELF=$(jq -e --arg ip "$MY_IP" '.machines[] | select(.ip == $ip)' "$CONFIG") \
  || die "this machine's IP ($MY_IP) is not in the machine list"
MY_NAME=$(jq -r .name <<<"$SELF")
MY_ROLE=$(jq -r .role <<<"$SELF")
echo "This machine: $MY_NAME ($MY_ROLE)"

# ---------- packages --------------------------------------------------------
pkgs=(lan-mouse syncthing wl-clipboard python-gobject gtk4 libadwaita)
[ "$MY_ROLE" = desktop ] && pkgs+=(wayvnc) || pkgs+=(tigervnc)
missing=()
for p in "${pkgs[@]}"; do pacman -Q "$p" &>/dev/null || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "--- installing packages: ${missing[*]}"
  if command -v omarchy >/dev/null; then omarchy pkg add "${missing[@]}"
  else sudo pacman -S --needed "${missing[@]}"; fi
fi

# ---------- binaries and configs -------------------------------------------
echo "--- installing to ~/.local/bin"
mkdir -p ~/.local/bin ~/.local/share/applications ~/Drop/incoming ~/.config/systemd/user
install -m755 "$DIR/bin/multibox" ~/.local/bin/multibox
install -m755 "$DIR/clipsync/clipsync.py" ~/.local/bin/clipsync.py
install -m755 "$DIR/dropzone/dropzone.py" ~/.local/bin/dropzone.py
install -m755 "$DIR/setup/multibox-setup.py" ~/.local/bin/multibox-setup.py
[ "$MY_ROLE" = desktop ] && install -m755 "$DIR/bin/extend-display" ~/.local/bin/extend-display

echo "--- systemd user services"
cat > ~/.config/systemd/user/clipsync.service <<EOF
[Unit]
Description=clipsync — tailnet clipboard sync + file receiver
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 $HOME/.local/bin/clipsync.py daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
cat > ~/.config/systemd/user/lan-mouse.service <<'EOF'
[Unit]
Description=lan-mouse software KVM (daemon)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=/usr/bin/lan-mouse daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now clipsync.service lan-mouse.service syncthing.service

echo "--- generating configs (multibox apply)"
"$HOME/.local/bin/multibox" apply

echo "--- syncthing Drop folder"
sleep 2
syncthing cli config folders add --id drop --label "Drop" --path ~/Drop 2>/dev/null || true

echo "--- desktop entries"
cat > ~/.local/share/applications/Dropzone.desktop <<EOF
[Desktop Entry]
Name=Dropzone
Comment=Drag files here to send them to the other machines
Exec=/usr/bin/python3 $HOME/.local/bin/dropzone.py
Terminal=false
Type=Application
Categories=Network;FileTransfer;
EOF
cat > ~/.local/share/applications/MultiboxSetup.desktop <<EOF
[Desktop Entry]
Name=Multibox Setup
Comment=Arrange the fleet's seats and options
Exec=$HOME/.local/bin/multibox setup
Terminal=false
Type=Application
Categories=Settings;
EOF
if [ "$MY_ROLE" != desktop ]; then
  # desktop-monitor itself is generated by 'multibox apply'
  cat > ~/.local/share/applications/DesktopMonitor.desktop <<EOF
[Desktop Entry]
Name=Desktop Monitor
Comment=Fullscreen slice of the desktop (extend-display)
Exec=$HOME/.local/bin/desktop-monitor
Terminal=false
Type=Application
Categories=Network;
EOF
fi

# ---------- omarchy bar widget ---------------------------------------------
if command -v omarchy-shell >/dev/null 2>&1; then
  PLUGDIR="$HOME/.config/omarchy/plugins/multibox"
  if [ ! -e "$PLUGDIR" ]; then
    echo "--- omarchy bar widget"
    mkdir -p "$PLUGDIR/plugin"
    cp "$DIR/manifest.json" "$PLUGDIR/"
    cp "$DIR/plugin/BarWidget.qml" "$PLUGDIR/plugin/"
    omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
    omarchy plugin enable multibox --section right 2>/dev/null \
      || echo "enable the widget later with: omarchy plugin enable multibox"
  else
    cp "$DIR/manifest.json" "$PLUGDIR/"
    cp "$DIR/plugin/BarWidget.qml" "$PLUGDIR/plugin/"
    echo "--- omarchy bar widget refreshed"
  fi
fi

# ---------- pairing ---------------------------------------------------------
echo "--- pairing"
"$HOME/.local/bin/multibox" pairing-info || true
"$HOME/.local/bin/multibox" pair || true

cat <<EOF

=== DONE on $MY_NAME ($MY_ROLE) ===
Next:
  1. Run this installer on every other machine in the list.
  2. Then run 'multibox pair' on EACH machine — it exchanges lan-mouse
     fingerprints and syncthing device IDs from the pairing files above
     (sent over the tailnet, or synced via ~/Drop once syncthing pairs;
     worst case copy ~/Drop/multibox-pairing/*.json between machines by hand).
  3. Check with 'multibox status'.
EOF
