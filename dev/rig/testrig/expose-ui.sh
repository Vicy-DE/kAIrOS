#!/bin/sh
# expose-ui.sh [R66] — runs INSIDE a kAIrOS guest (pipe it over ssh). Makes the
# kernel listen on all interfaces so the testrig host reaches the UI/API through the
# QEMU hostfwd on :1337 (those connections arrive from 10.0.2.2, not loopback).
#
# Since R66 the kernel binds 127.0.0.1 by default: the kiosk is on the machine, the
# API runs as root, and the image joins WiFi. Exposing it is this explicit, per-guest
# opt-in (a systemd drop-in, survives reboots of the overlay). Even exposed, the
# routes that change the machine (install/format/power/settings/downloads) refuse
# non-loopback callers without this boot's token — print it with:  cat /run/kairos/token
# and send it as the X-Kairos-Token header (or open the UI with ?token=<value>).
# Idempotent: restarts kairos only when the drop-in is new. Prints the token path.
set -u
D=/etc/systemd/system/kairos.service.d
C="$D/expose-ui.conf"
WANT='[Service]
# testrig / remote monitoring: reachable from the network (pod/testrig/expose-ui.sh) [R66]
Environment=VIBEOS_HOST=0.0.0.0'
if [ -f "$C" ] && [ "$(cat "$C")" = "$WANT" ]; then
    echo "expose-ui: already exposed (kairos listens on 0.0.0.0)"
else
    mkdir -p "$D" && printf '%s\n' "$WANT" > "$C" && systemctl daemon-reload
    systemctl restart kairos && echo "expose-ui: drop-in written, kairos restarted on 0.0.0.0"
fi
# wait for the kernel to answer again (a restart takes a few seconds)
i=0; while [ $i -lt 40 ] && ! curl -fsS -m 2 http://127.0.0.1:1337/api/status >/dev/null 2>&1; do i=$((i+1)); sleep 1; done
[ -r /run/kairos/token ] && echo "expose-ui: token for remote machine-changing calls: /run/kairos/token" \
                         || echo "expose-ui: (pre-R66 kernel in this guest: no token file, API fully open)"
exit 0
