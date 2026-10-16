#!/usr/bin/env bash
# dev/issues/create-issues.sh — create the labels and seed issues of dev/issues/seed-issues.json on GitHub
# through the REST API. Idempotent: labels that exist are left alone (colour/description untouched),
# issues whose title already exists (open or closed) are skipped.
#
#   GITHUB_TOKEN=<token> bash dev/issues/create-issues.sh          # create
#   bash dev/issues/create-issues.sh --dry-run                      # print what would be created, no token needed
#   GITHUB_REPO=owner/name …                                        # another repository (default Vicy-DE/kAIrOS)
#
# The token needs the `issues: write` permission (fine-grained) or the `repo` scope (classic).
# Needs curl + python3. Nothing in seed-issues.json is secret; read it before you run this.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SEED=${KAIROS_SEED_ISSUES:-$HERE/seed-issues.json}
REPO=${GITHUB_REPO:-Vicy-DE/kAIrOS}
API=${GITHUB_API:-https://api.github.com}
DRY=0
case "${1:-}" in
  --dry-run|-n) DRY=1 ;;
  "") ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac
[ -f "$SEED" ] || { echo "seed file not found: $SEED" >&2; exit 1; }
python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d.get("labels"), list) and isinstance(d.get("issues"), list)' "$SEED" \
  || { echo "seed file is not valid: $SEED" >&2; exit 1; }

if [ "$DRY" = 0 ]; then
  [ -n "${GITHUB_TOKEN:-}" ] || { echo "GITHUB_TOKEN is not set (use --dry-run to preview)" >&2; exit 1; }
fi

# gh(method, path, [json body]) -> response body on stdout; fails on HTTP >= 400
gh(){
  local method=$1 path=$2 body=${3:-}
  local args=(-sS -X "$method" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28"
              -H "Authorization: Bearer $GITHUB_TOKEN" --fail-with-body)
  [ -n "$body" ] && args+=(-H "Content-Type: application/json" --data-binary "$body")
  curl "${args[@]}" "$API$path"
}
# paginated GET: concatenates the JSON arrays of every page into one array
gh_all(){
  local path=$1 page=1 out="[]" chunk n
  while :; do
    chunk=$(gh GET "$path$( [[ "$path" == *\?* ]] && echo '&' || echo '?' )per_page=100&page=$page")
    n=$(python3 -I -c 'import json,sys; print(len(json.loads(sys.stdin.read())))' <<<"$chunk")
    out=$(python3 -I -c 'import json,sys; a=json.loads(sys.argv[1]); b=json.loads(sys.stdin.read()); print(json.dumps(a+b))' "$out" <<<"$chunk")
    [ "$n" -lt 100 ] && break
    page=$((page + 1))
  done
  printf '%s\n' "$out"
}

echo "== repository $REPO ($([ "$DRY" = 1 ] && echo DRY RUN || echo live)) =="
if [ "$DRY" = 1 ]; then
  EXISTING_LABELS="[]"; EXISTING_TITLES="[]"
else
  EXISTING_LABELS=$(gh_all "/repos/$REPO/labels" | python3 -I -c 'import json,sys; print(json.dumps([l["name"] for l in json.load(sys.stdin)]))')
  EXISTING_TITLES=$(gh_all "/repos/$REPO/issues?state=all" | python3 -I -c 'import json,sys; print(json.dumps([i["title"] for i in json.load(sys.stdin) if "pull_request" not in i]))')
fi

# ---- labels ----
while IFS= read -r line; do
  name=$(python3 -I -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$line")
  if python3 -I -c 'import json,sys; sys.exit(0 if sys.argv[1] in json.loads(sys.argv[2]) else 1)' "$name" "$EXISTING_LABELS"; then
    echo "label exists:  $name"; continue
  fi
  if [ "$DRY" = 1 ]; then echo "label create:  $name"; continue; fi
  gh POST "/repos/$REPO/labels" "$line" >/dev/null && echo "label created: $name"
done < <(python3 -I -c 'import json,sys
for l in json.load(open(sys.argv[1]))["labels"]:
    print(json.dumps({"name": l["name"], "color": l.get("color", "ededed"), "description": l.get("description", "")[:100]}))' "$SEED")

# ---- issues ----
CREATED=0; SKIPPED=0
while IFS= read -r line; do
  title=$(python3 -I -c 'import json,sys; print(json.loads(sys.argv[1])["title"])' "$line")
  if python3 -I -c 'import json,sys; sys.exit(0 if sys.argv[1] in json.loads(sys.argv[2]) else 1)' "$title" "$EXISTING_TITLES"; then
    echo "issue exists:  $title"; SKIPPED=$((SKIPPED + 1)); continue
  fi
  if [ "$DRY" = 1 ]; then
    echo "issue create:  $title"
    python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); print("   labels:", ", ".join(d["labels"])); print("   body:", len(d["body"]), "chars")' "$line"
    CREATED=$((CREATED + 1)); continue
  fi
  url=$(gh POST "/repos/$REPO/issues" "$line" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["html_url"])')
  echo "issue created: $title -> $url"; CREATED=$((CREATED + 1))
  sleep 1   # GitHub asks for a pause between content-creating requests
done < <(python3 -I -c 'import json,sys
for i in json.load(open(sys.argv[1]))["issues"]:
    print(json.dumps({"title": i["title"], "body": i["body"], "labels": i.get("labels", [])}))' "$SEED")
echo "== $CREATED issue(s) $([ "$DRY" = 1 ] && echo 'would be created' || echo created), $SKIPPED skipped =="
