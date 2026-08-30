#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
REPORT=/root/tg-repair-stage1-$STAMP.txt
BACKUP=/root/tg-agent-pre-repair-$STAMP.tgz
exec > >(tee "$REPORT") 2>&1

echo '=== TG JOB AGENT REPAIR STAGE 1 ==='
date -u

# Safety first: no outbound worker while repairing shared Telethon session.
systemctl stop tg-job-agent.service 2>/dev/null || true
systemctl stop tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer 2>/dev/null || true
systemctl stop tg-job-discovery.service tg-job-scanner.service tg-job-selector.service 2>/dev/null || true
pkill -f '/opt/tg-job-agent/worker.py' 2>/dev/null || true
sleep 2

echo '=== backup ==='
tar --exclude='*.session-journal' -czf "$BACKUP" "$APP"
ls -lh "$BACKUP"

echo '=== process check ==='
ps auxww | grep -Ei 'tg-job-agent|worker.py|scanner.py|discover_sources.py|selector.py|telethon' | grep -v grep || true

echo '=== TelegramClient declarations (secrets not printed) ==='
grep -RniE 'TelegramClient|session' "$APP"/*.py 2>/dev/null | sed -E 's/(api_hash|TG_API_HASH|AIRTABLE_TOKEN)[^,)]*/\1=<hidden>/Ig' || true

echo '=== systemd unit contents ==='
for u in tg-job-agent.service tg-job-discovery.service tg-job-scanner.service tg-job-selector.service tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer; do
  echo "--- $u"
  systemctl cat "$u" 2>/dev/null || true
done

echo '=== session files ==='
ls -lah "$APP"/*.session* 2>/dev/null || true

# Clear stale WAL/journal only after all TG processes are stopped.
rm -f "$APP"/telegram.session-journal "$APP"/telegram.session-wal "$APP"/telegram.session-shm 2>/dev/null || true

# Validate primary Telegram session sqlite integrity without changing auth.
python3 - <<'PY'
import sqlite3, os
p='/opt/tg-job-agent/telegram.session'
print('session_exists:', os.path.exists(p), 'bytes:', os.path.getsize(p) if os.path.exists(p) else 0)
if os.path.exists(p):
    c=sqlite3.connect(p, timeout=5)
    print('session_integrity:', c.execute('pragma integrity_check').fetchone()[0])
    print('tables:', [r[0] for r in c.execute("select name from sqlite_master where type='table'")])
    c.close()
PY

# Create independent session copies for concurrent read clients. This removes
# SQLite cross-process locking while preserving the same authorized account.
for role in worker scanner discovery; do
  cp -f "$APP/telegram.session" "$APP/telegram_${role}.session"
  chown --reference="$APP/telegram.session" "$APP/telegram_${role}.session" 2>/dev/null || true
  chmod --reference="$APP/telegram.session" "$APP/telegram_${role}.session" 2>/dev/null || true
done

echo '=== database integrity ==='
python3 - <<'PY'
import sqlite3, os
p='/opt/tg-job-agent/telegram_jobs.db'
print('db_exists:',os.path.exists(p),'bytes:',os.path.getsize(p) if os.path.exists(p) else 0)
if os.path.exists(p):
 c=sqlite3.connect(p, timeout=10)
 print('integrity:', c.execute('pragma integrity_check').fetchone()[0])
 for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
  try: n=c.execute(f'select count(*) from "{t}"').fetchone()[0]
  except Exception as e: n=f'ERR {e}'
  print('table',t,'rows',n)
 c.close()
PY

echo '=== dry service runs with worker STOPPED ==='
for s in tg-job-discovery.service tg-job-scanner.service tg-job-selector.service; do
  echo "--- RUN $s"
  systemctl reset-failed "$s" 2>/dev/null || true
  timeout 180 systemctl start --wait "$s" || true
  systemctl status "$s" --no-pager -l || true
  journalctl -u "$s" -n 80 --no-pager || true
done

echo '=== FINAL SAFETY STATE ==='
echo 'Outbound worker remains STOPPED. Timers remain STOPPED.'
systemctl is-active tg-job-agent.service || true
systemctl is-active tg-job-discovery.timer || true
systemctl is-active tg-job-scanner.timer || true
systemctl is-active tg-job-selector.timer || true

echo "REPORT=$REPORT"
echo "BACKUP=$BACKUP"
echo '=== END STAGE 1 ==='
