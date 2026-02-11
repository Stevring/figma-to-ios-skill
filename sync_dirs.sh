#!/usr/bin/env bash
set -euo pipefail

# Sync files between two directories using rsync.
#
# Safe defaults:
# - Does NOT delete extra files unless --delete is provided.
# - Default direction is one-way: A -> B (dirA is source, dirB is destination).
# - Two-way sync is available via --two-way (uses mtime/size heuristics; conflicts possible).
#
# Examples:
#   ./sync_dirs.sh path/to/A path/to/B
#   ./sync_dirs.sh --reverse path/to/A path/to/B            # B -> A
#   ./sync_dirs.sh --two-way path/to/A path/to/B            # best-effort bidirectional
#   ./sync_dirs.sh --watch --interval 2 path/to/A path/to/B # poll/watch and sync repeatedly
#   ./sync_dirs.sh --delete path/to/A path/to/B             # mirror A into B (danger)
#
# Notes:
# - Requires: rsync
# - --watch uses fswatch if installed; otherwise falls back to polling.

usage() {
  cat <<'EOF'
Usage:
  sync_dirs.sh [options] <dirA> <dirB>

Options:
  --reverse         Sync B -> A (default is A -> B)
  --two-way         Best-effort two-way sync: A -> B then B -> A (conflicts possible)
  --delete          Delete files in destination that don't exist in source (mirror mode)
  --dry-run         Print what would change, don't write
  --watch           Continuously sync when changes occur (fswatch if available; else polling)
  --interval N      Polling interval seconds for --watch without fswatch (default: 1)
  --exclude PAT     Additional rsync exclude pattern (repeatable)
  -h, --help        Show help

Exit codes:
  0 success
  2 usage error
EOF
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required command: $1" >&2
    exit 2
  fi
}

need_cmd rsync

reverse=0
two_way=0
do_delete=0
dry_run=0
watch=0
interval=1
excludes=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reverse) reverse=1; shift ;;
    --two-way) two_way=1; shift ;;
    --delete) do_delete=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --watch) watch=1; shift ;;
    --interval) interval="${2:-}"; shift 2 ;;
    --exclude) excludes+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "error: unknown option: $1" >&2; usage; exit 2 ;;
    *) break ;;
  esac
done

if [[ $# -ne 2 ]]; then
  echo "error: expected <dirA> <dirB>" >&2
  usage
  exit 2
fi

dirA="$1"
dirB="$2"

# Validate/create directories. Treat the "source" as required; create "destination" if missing.
if [[ "$two_way" -eq 1 ]]; then
  mkdir -p "$dirA" "$dirB"
elif [[ "$reverse" -eq 1 ]]; then
  if [[ ! -d "$dirB" ]]; then
    echo "error: source directory (dirB) does not exist: $dirB" >&2
    exit 2
  fi
  mkdir -p "$dirA"
else
  if [[ ! -d "$dirA" ]]; then
    echo "error: source directory (dirA) does not exist: $dirA" >&2
    exit 2
  fi
  mkdir -p "$dirB"
fi

if [[ "$two_way" -eq 1 && "$reverse" -eq 1 ]]; then
  echo "error: --two-way cannot be combined with --reverse" >&2
  exit 2
fi

if [[ ! "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "error: --interval must be a number (seconds)" >&2
  exit 2
fi

# Default excludes: avoid syncing typical junk and repo internals.
default_excludes=(
  ".git/"
  ".DS_Store"
  "__pycache__/"
  "*.pyc"
  ".venv/"
  "node_modules/"
  ".ai_tmp/"
)

rsync_args=(-a --update --no-perms --no-owner --no-group)
if [[ "$dry_run" -eq 1 ]]; then
  rsync_args+=(--dry-run -v)
else
  rsync_args+=(-v)
fi
if [[ "$do_delete" -eq 1 ]]; then
  rsync_args+=(--delete)
fi

for pat in "${default_excludes[@]}"; do
  rsync_args+=(--exclude "$pat")
done
# bash with `set -u` treats empty arrays as "unbound" when expanded, so guard.
if ((${#excludes[@]})); then
  for pat in "${excludes[@]}"; do
    if [[ -n "$pat" ]]; then
      rsync_args+=(--exclude "$pat")
    fi
  done
fi

sync_one_way() {
  local src="$1"
  local dst="$2"
  mkdir -p "$dst"
  # Sync contents of src into dst.
  rsync "${rsync_args[@]}" -- "$src"/ "$dst"/
}

sync_once() {
  if [[ "$two_way" -eq 1 ]]; then
    echo "== Two-way sync (best-effort): $dirA <-> $dirB ==" >&2
    echo "note: conflicts are resolved by mtime/size heuristics; consider using git for true merging." >&2
    sync_one_way "$dirA" "$dirB"
    sync_one_way "$dirB" "$dirA"
    return 0
  fi

  if [[ "$reverse" -eq 1 ]]; then
    echo "== Sync: $dirB -> $dirA ==" >&2
    sync_one_way "$dirB" "$dirA"
  else
    echo "== Sync: $dirA -> $dirB ==" >&2
    sync_one_way "$dirA" "$dirB"
  fi
}

watch_with_fswatch() {
  local watch_roots=("$@")

  compute_snapshot() {
    # Hash file contents to suppress duplicate sync loops from our own rsync writes.
    # Prune common noisy directories.
    find "${watch_roots[@]}" \
      \( -name .git -o -name .ai_tmp -o -name .venv -o -name node_modules -o -name __pycache__ \) -prune -o \
      -type f ! -name .DS_Store -print0 2>/dev/null | \
      sort -z | \
      xargs -0 shasum 2>/dev/null | \
      shasum | awk '{print $1}'
  }

  local prev
  prev="$(compute_snapshot || true)"

  # -r recursive
  # -o emit one event per batch (debounce bursts from editors)
  # -l latency seconds
  fswatch -r -o -l "$interval" -- "${watch_roots[@]}" | while IFS= read -r _; do
    cur="$(compute_snapshot || true)"
    if [[ -n "$cur" && "$cur" == "$prev" ]]; then
      continue
    fi
    sync_once || true
    prev="$(compute_snapshot || true)"
  done
}

watch_with_polling() {
  local watch_roots=("$@")

  compute_snapshot() {
    find "${watch_roots[@]}" \
      \( -name .git -o -name .ai_tmp -o -name .venv -o -name node_modules -o -name __pycache__ \) -prune -o \
      -type f ! -name .DS_Store -print0 2>/dev/null | \
      sort -z | \
      xargs -0 shasum 2>/dev/null | \
      shasum | awk '{print $1}'
  }

  local prev
  prev="$(compute_snapshot || true)"
  while true; do
    cur="$(compute_snapshot || true)"
    if [[ -n "$cur" && "$cur" != "$prev" ]]; then
      sync_once || true
      prev="$(compute_snapshot || true)"
    fi
    sleep "$interval"
  done
}

if [[ "$watch" -eq 1 ]]; then
  sync_once
  if command -v fswatch >/dev/null 2>&1; then
    # Avoid feedback loops: in one-way sync, watch only the source side.
    if [[ "$two_way" -eq 1 ]]; then
      watch_with_fswatch "$dirA" "$dirB"
    elif [[ "$reverse" -eq 1 ]]; then
      watch_with_fswatch "$dirB"
    else
      watch_with_fswatch "$dirA"
    fi
  else
    echo "note: fswatch not found; falling back to polling every ${interval}s" >&2
    if [[ "$two_way" -eq 1 ]]; then
      watch_with_polling "$dirA" "$dirB"
    elif [[ "$reverse" -eq 1 ]]; then
      watch_with_polling "$dirB"
    else
      watch_with_polling "$dirA"
    fi
  fi
else
  sync_once
fi
