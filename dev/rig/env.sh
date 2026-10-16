# dev/rig/env.sh — sourced by the host-side helper scripts (desk-window.sh, stick-logs.sh,
# stick-patch.sh, gpu-to-*.sh, make-key.sh). Loads dev/rig/.env when present and fills the
# defaults, so every script sees the same KAIROS_* paths docker compose uses.
#   RIG_DIR            this directory
#   REPO_DIR           the repository root
#   KAIROS_HOME        images, overlays, .gpu.lock              (default: $RIG_DIR/work)
#   KAIROS_MODELS      model files                               (default: $KAIROS_HOME/models)
#   KAIROS_KNOWLEDGE   *.zim                                     (default: $KAIROS_HOME/knowledge)
#   KAIROS_BIN         kiwix-serve + engine binaries             (default: $KAIROS_HOME/bin)
#   KAIROS_ENGINES     a built engine set (bin/ + engines/ + lib/) (default: $KAIROS_HOME/engines)
#   KAIROS_GPU_PCI / KAIROS_GPU_AUDIO_PCI / KAIROS_GPU_IDS       the NVIDIA GPU's PCI functions / IDs
#   KAIROS_GPU_LOCK    the flock file every GPU job must hold    ($KAIROS_HOME/.gpu.lock)
# Relative paths in .env are relative to this directory.
RIG_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$RIG_DIR/../.." && pwd)
if [ -f "$RIG_DIR/.env" ]; then
  # KEY=VALUE lines only (what docker compose accepts); comments and blanks skipped; no expansion
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key=${line%%=*}; val=${line#*=}
    case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
    # the environment wins over the file (docker compose does the same)
    [ -n "${!key:-}" ] || export "$key=$val"
  done < "$RIG_DIR/.env"
fi
_abs(){ case "$1" in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$RIG_DIR/${1#./}" ;; esac; }
export KAIROS_HOME=$(_abs "${KAIROS_HOME:-./work}")
export KAIROS_MODELS=$(_abs "${KAIROS_MODELS:-$KAIROS_HOME/models}")
export KAIROS_KNOWLEDGE=$(_abs "${KAIROS_KNOWLEDGE:-$KAIROS_HOME/knowledge}")
export KAIROS_BIN=$(_abs "${KAIROS_BIN:-$KAIROS_HOME/bin}")
export KAIROS_ENGINES=$(_abs "${KAIROS_ENGINES:-$KAIROS_HOME/engines}")
export KAIROS_GPU_PCI=${KAIROS_GPU_PCI:-0000:01:00.0}
export KAIROS_GPU_AUDIO_PCI=${KAIROS_GPU_AUDIO_PCI:-0000:01:00.1}
export KAIROS_GPU_IDS=${KAIROS_GPU_IDS:-10de:2820,10de:22bd}
export KAIROS_GPU_LOCK=${KAIROS_GPU_LOCK:-$KAIROS_HOME/.gpu.lock}
export VFIO_GROUP=${VFIO_GROUP:-14}
