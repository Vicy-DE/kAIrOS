#!/bin/sh
# gpu-display-setup.sh [R65] — make the kAIrOS kiosk render on the passed-through
# NVIDIA GPU (headless virtual display) and expose that GPU framebuffer over VNC
# (x11vnc) so the desktop can be watched exactly as a real dGPU-driven monitor.
# Runs INSIDE the guest. Idempotent.
set -u
say(){ printf '\033[1;35m[gpu-display]\033[0m %s\n' "$*"; }

# 1. NVIDIA modules for DISPLAY (not just compute): modeset + drm
say "loading nvidia display modules"
modprobe nvidia 2>/dev/null || { echo "nvidia module missing"; exit 1; }
modprobe nvidia_modeset 2>/dev/null || true
modprobe nvidia_drm modeset=1 2>/dev/null || true

# 2. the Xorg DDX (nvidia_drv.so) must exist for X to drive the GPU
if ! ls /usr/lib/xorg/modules/drivers/nvidia_drv.so >/dev/null 2>&1; then
    say "nvidia Xorg driver missing — installing (needs guest internet)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -o Acquire::Retries=3 >/dev/null 2>&1
    apt-get install -y --no-install-recommends xserver-xorg-video-nvidia >/dev/null 2>&1 \
        || apt-get install -y --no-install-recommends nvidia-driver-libs-nonfree >/dev/null 2>&1 \
        || say "WARN: could not install nvidia_drv.so — X will fall back"
fi

# 3. headless virtual display: pin X to the NVIDIA GPU with a fake 1080p panel
BUS=$(lspci -D 2>/dev/null | awk '/NVIDIA/ && /VGA|3D/{print $1; exit}')
if [ -n "$BUS" ]; then
    # 0000:00:06.0 -> PCI:0:6:0  (nvidia wants decimal bus:dev:func, domain dropped)
    b=${BUS#*:}; bus=${b%%:*}; rest=${b#*:}; dev=${rest%.*}; fn=${rest#*.}
    PCI=$(printf 'PCI:%d:%d:%d' "0x$bus" "0x$dev" "0x$fn")
    say "nvidia at $BUS -> $PCI"
    mkdir -p /etc/X11/xorg.conf.d
    cat > /etc/X11/xorg.conf.d/10-kairos-nvidia.conf <<EOF
Section "ServerLayout"
    Identifier "kairos"
    Screen 0 "nvscreen"
EndSection
Section "Device"
    Identifier "nvidia"
    Driver "nvidia"
    BusID "$PCI"
    Option "AllowEmptyInitialConfiguration" "true"
    Option "ConnectedMonitor" "DFP-0"
EndSection
Section "Screen"
    Identifier "nvscreen"
    Device "nvidia"
    Option "UseDisplayDevice" "DFP-0"
    Option "ModeValidation" "NoDFPNativeResolutionCheck,NoVirtualSizeCheck,NoMaxPClkCheck,AllowNonEdidModes,NoEdidMaxPClkCheck"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual 1920 1080
        Modes "1920x1080"
    EndSubSection
EndSection
EOF
else
    say "WARN: no NVIDIA VGA device in guest lspci — is passthrough on?"
fi

# 4. Chromium GPU flags (not swiftshader). NB: the kiosk's .xinitrc reads runtime flags
# from /etc/kairos/chromium.flags and /run/kairos/chromium.flags on every launch — this
# file is deliberately NOT one of them: the 4070 kiosk renders fine without these
# (VM test 2026-09-11 §V5); copy it to /etc/kairos/chromium.flags to opt in, then
# `touch /run/kairos/kiosk-reload`.
mkdir -p /etc/kairos
cat > /etc/kairos/chromium-gpu.flags <<'EOF'
--ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy --use-gl=egl
EOF

# 5. x11vnc: publish the GPU X framebuffer on :5900 (forwarded to the host)
if ! command -v x11vnc >/dev/null 2>&1; then
    say "installing x11vnc (needs guest internet)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -o Acquire::Retries=3 >/dev/null 2>&1
    apt-get install -y --no-install-recommends x11vnc >/dev/null 2>&1 || say "WARN: x11vnc install failed"
fi
# Rig unit, not a product unit. It used to race X (Restart=always, 3 s, no start limit):
# 788 "Failed with result" lines in 44 min and a permanently non-empty `systemctl
# --failed` in one session (VM test 2026-09-11 §V1). Now: it only STARTS once the kiosk
# X socket exists (ExecStartPre waits up to 10 min, then the start fails cleanly), X
# going away restarts it 5 s later, and 20 failures in 10 min end it in `failed`
# instead of looping for ever.
cat > /etc/systemd/system/kairos-x11vnc.service <<'EOF'
[Unit]
Description=kAIrOS GPU desktop over VNC (x11vnc on the nvidia X screen)
After=multi-user.target getty@tty1.service
StartLimitIntervalSec=600
StartLimitBurst=20
[Service]
Type=simple
# wait for the kiosk X to exist (the tty1 autologin starts it), then mirror it on :5900
ExecStartPre=/bin/sh -c 'for i in $(seq 1 600); do [ -S /tmp/.X11-unix/X0 ] && exit 0; sleep 1; done; exit 1'
ExecStart=/usr/bin/x11vnc -display :0 -auth guess -forever -shared -nopw -rfbport 5900 -noshm -noxdamage -o /var/log/x11vnc.log
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
command -v x11vnc >/dev/null 2>&1 && systemctl enable --now kairos-x11vnc.service 2>/dev/null

# 6. [R66] keep the API reachable from the host (the kernel binds loopback by default)
if [ -f /etc/systemd/system/kairos.service.d/expose-ui.conf ]; then
    say "kAIrOS API already exposed to the host"
else
    mkdir -p /etc/systemd/system/kairos.service.d
    printf '[Service]\n# testrig / remote monitoring: reachable from the network (pod/testrig/expose-ui.sh) [R66]\nEnvironment=VIBEOS_HOST=0.0.0.0\n' \
        > /etc/systemd/system/kairos.service.d/expose-ui.conf
    systemctl daemon-reload; systemctl restart kairos
    say "kAIrOS API exposed to the host (drop-in written, kairos restarted)"
fi

# 7. restart the kiosk so X comes up on the NVIDIA GPU
say "restarting the kiosk on the GPU"
pkill -x mpv 2>/dev/null || true
systemctl restart getty@tty1 2>/dev/null || true
say "done — GPU desktop should appear on VNC :5900"
