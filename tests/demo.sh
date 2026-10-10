#!/usr/bin/env bash
# Live end-to-end demonstration against the REAL kAIrOS kernel on :1337.
# Exercises every subsystem with the actual local models; saves artifacts to /tmp/kairos-demo.
set -u
B=http://127.0.0.1:1337
D=/tmp/kairos-demo
mkdir -p $D
say() { echo; echo "════ $* ════"; }

say "0. OS identity (/api/status)"
curl -s $B/api/status | python3 -m json.tool | head -12

say "1. LIVING APP — generate v1, then relaunch to evolve to v2 (real 7B model)"
gen() { curl -s -N -X POST $B/api/app -H 'Content-Type: application/json' \
        -d "{\"prompt\":\"$1\",\"app_id\":\"$2\"}" 2>/dev/null; }
gen "a futuristic pomodoro timer with a start and reset button" demo-pomodoro > $D/pom-v1.sse
V1=$(grep '^data:' $D/pom-v1.sse | tail -1 | python3 -c 'import sys,json;print(json.loads(sys.stdin.read()[6:]).get("version"))')
echo "  v1 generated (version=$V1, $(grep -c '^event: delta' $D/pom-v1.sse) token-deltas streamed)"
gen "a futuristic pomodoro timer with a start and reset button" demo-pomodoro > $D/pom-v2.sse
V2=$(grep '^data:' $D/pom-v2.sse | tail -1 | python3 -c 'import sys,json;print(json.loads(sys.stdin.read()[6:]).get("version"))')
echo "  relaunch -> version=$V2 (living software: code evolved, not re-run)"
echo "  history on disk:"; ls -1 /opt/vibeos/cache/apps/demo-pomodoro/history/ 2>/dev/null | sed 's/^/    /'
echo "  saved prompt:"; python3 -c "import json;print('   ',json.load(open('/opt/vibeos/cache/apps/demo-pomodoro/meta.json'))['prompt'])"

say "2. IMAGINARY WEB — browser generates a whole site (grounded by search), then caches it"
curl -s -X POST $B/api/browse -H 'Content-Type: application/json' \
  -d '{"address":"kairos.news/quantum-computing"}' > $D/page1.json
python3 -c "import json;d=json.load(open('$D/page1.json'));print('  generated',len(d['html']),'bytes, grounded_by:',d.get('grounded_by'),'cached:',d['cached']);print('  has link-interceptor:', 'kairosNavigate' in d['html'])"
curl -s -X POST $B/api/browse -H 'Content-Type: application/json' -d '{"address":"kairos.news/quantum-computing"}' > $D/page2.json
python3 -c "import json;print('  revisit cached:',json.load(open('$D/page2.json'))['cached'],'(offline-replayable)')"

say "3. DESKTOP SEARCH — always finds something; nonsense query still yields create-actions"
curl -s "$B/api/desktop-search?q=zzqx-nonsense" | python3 -c "import sys,json;d=json.load(sys.stdin);print('  results:',len(d['results']),'| groups:',sorted({r['group'] for r in d['results']}));print('  e.g.:',d['results'][0]['title'])"

say "4. OFFLINE KNOWLEDGE — real Simple-Wikipedia lookup via kiwix"
curl -s -X POST $B/api/search -H 'Content-Type: application/json' -d '{"query":"Isaac Newton"}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('  online:',d['online'],'source:',d['source']);[print('   -',r['title']+':',r['snippet'][:90]) for r in d['results'][:2]]"

say "5. ANDROID FILESYSTEM — /data writable, /system read-only"
curl -s -X POST $B/api/fs -H 'Content-Type: application/json' -d '{"op":"write","path":"/data/demo/note.txt","content":"hello from an app"}' >/dev/null
echo "  wrote /data/demo/note.txt -> read back: $(curl -s -X POST $B/api/fs -H 'Content-Type: application/json' -d '{"op":"read","path":"/data/demo/note.txt"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["content"])')"
echo "  write to /system -> $(curl -s -o /dev/null -w '%{http_code}' -X POST $B/api/fs -H 'Content-Type: application/json' -d '{"op":"write","path":"/system/x","content":"y"}') (403 = protected)"

say "6. REAL IMAGE — Z-Image via the kernel's job queue (random seed)"
JID=$(curl -s -X POST $B/api/image -H 'Content-Type: application/json' -d '{"prompt":"a serene futuristic city"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["job"])')
echo "  job $JID dispatched; polling…"
for i in $(seq 1 60); do
  R=$(curl -s $B/api/job/$JID)
  S=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$S" = done ] && { echo "  done: $(echo "$R" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["result"],"in",d["elapsed"],"s")')"; break; }
  [ "$S" = error ] && { echo "  ERROR: $R"; break; }
  sleep 3
done

say "DEMO COMPLETE — artifacts in $D"
