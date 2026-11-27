#!/usr/bin/env bash
# Write one complete backup of bran's data to standard output as a .tar.gz.
#
# Runs on the server as the user that owns the data. The laptop's
# scripts/pull_backup.ps1 sends it over ssh and saves what comes back:
#
#   ssh bran@<server> bash -s < scripts/backup_stream.sh > bran.tar.gz
#
# In the archive:
#   bran.sqlite        a consistent snapshot of the live database (taken with
#                      SQLite's backup API, so it is safe while bran is running)
#   briefings/ documents/ projects/ uploads/     the files bran has written
#   .claude/projects/  the Claude session transcripts chats resume from
#
# Left out on purpose: .env and the Claude sign-in (secrets stay on the server),
# caches, logs and the server's own database snapshots.
set -euo pipefail

DATA="${BRAN_HOME:-$HOME/.bran}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 - "$DATA/bran.sqlite" "$TMP/bran.sqlite" >&2 <<'EOF'
import sqlite3
import sys

src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
check = dst.execute("pragma integrity_check").fetchone()[0]
dst.close()
src.close()
if check != "ok":
    sys.exit(f"database snapshot failed its integrity check: {check}")
EOF

args=(-C "$TMP" bran.sqlite)
for d in briefings documents projects uploads; do
  [ -d "$DATA/$d" ] && args+=(-C "$DATA" "$d")
done
[ -d "$HOME/.claude/projects" ] && args+=(-C "$HOME" .claude/projects)

# A transcript can grow while it is being read; tar reports that as exit 1.
# The archive is still complete, so only a real error (2) fails the backup.
tar -czf - "${args[@]}" || { rc=$?; [ "$rc" -eq 1 ] || exit "$rc"; }
