#!/usr/bin/env bash
# feature-test.sh — runs INSIDE THE GUEST (piped over ssh). Exercises the user-facing
# kAIrOS features end to end on the real GPU and prints one PASS/FAIL line per feature.
# Results also land in /tmp/feature-results.txt inside the guest.
exec > >(tee /tmp/feature-results.txt) 2>&1
K=http://127.0.0.1:1337
say(){ printf '=== %s ===\n' "$*"; }
poll_job(){ # $1=jid $2=max_iter
  for i in $(seq 1 "${2:-200}"); do
    S=$(curl -sf "$K/api/job/$1")
    echo "$S" | grep -q '"status": "done"'  && { echo "$S"; return 0; }
    echo "$S" | grep -q '"status": "error"' && { echo "$S"; return 1; }
    sleep 3
  done; echo TIMEOUT; return 1
}

say "1. app generation (LLM writes a full HTML app)"
R=$(curl -sf -X POST $K/api/app -H 'Content-Type: application/json' \
    -d '{"prompt":"simple calculator"}' --max-time 600)
echo "$R" | head -c 200; echo
echo "$R" | grep -qi '<html\|app_id\|"id"' && echo "FEATURE_APP=PASS" || echo "FEATURE_APP=FAIL"

say "2. python program / game forge (pygame, self-healing)"
R=$(curl -sf -X POST $K/api/game -H 'Content-Type: application/json' \
    -d '{"prompt":"pong with two paddles"}')
echo "$R"
JID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job"])' 2>/dev/null)
if [ -n "$JID" ]; then
  J=$(poll_job "$JID" 200); echo "$J" | tail -c 300
  echo "$J" | grep -q '"status": "done"' && echo "FEATURE_GAME=PASS" || echo "FEATURE_GAME=FAIL"
else echo "FEATURE_GAME=FAIL"; fi

say "3. browser ONLINE (imaginary web, grounded by live web search)"
R=$(curl -sf -X POST $K/api/browse -H 'Content-Type: application/json' \
    -d '{"address":"weather in berlin"}' --max-time 600)
echo "$R" | head -c 200; echo
echo "$R" | grep -qi '<html\|<body\|<!doctype' && echo "FEATURE_BROWSE_ONLINE=PASS" || echo "FEATURE_BROWSE_ONLINE=FAIL"

say "4. browser OFFLINE (same behavior, grounded by local encyclopedia)"
mkdir -p /etc/systemd/system/kairos.service.d
printf '[Service]\nEnvironment=VIBEOS_FORCE_OFFLINE=1\n' > /etc/systemd/system/kairos.service.d/offline-test.conf
systemctl daemon-reload && systemctl restart kairos && sleep 6
R=$(curl -sf -X POST $K/api/browse -H 'Content-Type: application/json' \
    -d '{"address":"albert einstein"}' --max-time 600)
echo "$R" | head -c 200; echo
echo "$R" | grep -qi '<html\|<body\|<!doctype' && echo "FEATURE_BROWSE_OFFLINE=PASS" || echo "FEATURE_BROWSE_OFFLINE=FAIL"
S=$(curl -sf -X POST $K/api/search -H 'Content-Type: application/json' -d '{"query":"einstein"}')
echo "$S" | head -c 150; echo
echo "$S" | grep -qi 'einstein' && echo "FEATURE_SEARCH_OFFLINE=PASS" || echo "FEATURE_SEARCH_OFFLINE=FAIL"
rm -f /etc/systemd/system/kairos.service.d/offline-test.conf
systemctl daemon-reload && systemctl restart kairos && sleep 6

say "5. video generation (Wan 2.2 on the 4070 — slow, be patient)"
R=$(curl -sf -X POST $K/api/video -H 'Content-Type: application/json' \
    -d '{"prompt":"a glowing cube rotating"}')
echo "$R"
JID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job"])' 2>/dev/null)
if [ -n "$JID" ]; then
  J=$(poll_job "$JID" 500); echo "$J" | tail -c 300
  echo "$J" | grep -q '"status": "done"' && echo "FEATURE_VIDEO=PASS" || echo "FEATURE_VIDEO=FAIL"
else echo "FEATURE_VIDEO=FAIL"; fi

say "SUMMARY"; grep -E '^FEATURE_' /tmp/feature-results.txt
