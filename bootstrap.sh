#!/usr/bin/env bash
set -euo pipefail

APP=/opt/tg-job-agent
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
REPORT=/root/tg-agent-report-$STAMP.txt
BACKUP=/root/tg-agent-backup-$STAMP.tgz

exec > >(tee "$REPORT") 2>&1

echo "=== TELEGRAM JOB AGENT SAFE BOOTSTRAP ==="
date -u
hostname
uname -a

echo "\n=== NETWORK ==="
ip -br addr || true
ip route || true
ss -lntp || true

echo "\n=== SYSTEMD MATCHES ==="
systemctl list-unit-files --type=service --no-pager | grep -Ei 'telegram|telethon|job.?agent|tg-' || true
systemctl list-unit-files --type=timer --no-pager | grep -Ei 'telegram|telethon|job.?agent|tg-' || true

echo "\n=== RUNNING PROCESSES ==="
ps auxww | grep -Ei 'telegram|telethon|tg-job-agent|job_agent' | grep -v grep || true

echo "\n=== CRON ==="
crontab -l 2>/dev/null || true
grep -RniE 'telegram|telethon|tg-job-agent|job_agent' /etc/cron* 2>/dev/null || true

if [ -d "$APP" ]; then
  echo "\n=== APP TREE ==="
  find "$APP" -maxdepth 3 -type f -printf '%p\n' 2>/dev/null | sort | head -500
  echo "\n=== APP DISK USAGE ==="
  du -sh "$APP" || true
  echo "\n=== SAFE BACKUP ==="
  tar --exclude='*.session-journal' -czf "$BACKUP" "$APP" 2>/dev/null || true
  ls -lh "$BACKUP" || true
else
  echo "APP DIRECTORY NOT FOUND: $APP"
fi

echo "\n=== ENV KEY NAMES ONLY (VALUES HIDDEN) ==="
for f in "$APP"/.env "$APP"/.env.* /etc/default/*telegram* /etc/default/*job*; do
  [ -f "$f" ] || continue
  echo "--- $f"
  sed -nE 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1=<hidden>/p' "$f" || true
done

echo "\n=== SQLITE / DATA FILES ==="
find "$APP" -maxdepth 4 -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.json' -o -name '*.csv' \) -printf '%p %s bytes\n' 2>/dev/null | sort || true

echo "\n=== RECENT JOURNAL MATCHES ==="
for u in $(systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -Ei 'telegram|telethon|job.?agent|tg-' || true); do
  echo "--- $u"
  journalctl -u "$u" -n 120 --no-pager 2>/dev/null || true
done

echo "\n=== SAFETY HOLD ==="
echo "No Telegram sends were enabled or triggered by this script."
echo "Report: $REPORT"
echo "Backup: $BACKUP"
echo "=== END ==="
