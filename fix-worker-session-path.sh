#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
W=$APP/worker.py
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo '=== FIX WORKER SESSION PATH ==='
cp -a "$W" "/root/worker.py.$STAMP.bak"
systemctl stop tg-job-agent.service
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/tg-job-agent/worker.py')
s=p.read_text()
# Normalize any accidental double suffix introduced by previous session patch.
s2=s.replace('/opt/tg-job-agent/telegram_worker.session_worker.session','/opt/tg-job-agent/telegram_worker.session')
s2=s2.replace('telegram_worker.session_worker.session','telegram_worker.session')
if s2==s:
    print('no double-suffix literal found; inspecting TelegramClient line')
else:
    p.write_text(s2)
    print('double-suffix fixed')
PY
python3 -m py_compile "$W"
# Remove only stale sidecars for the dedicated worker session, never the session itself.
rm -f "$APP/telegram_worker.session-journal" "$APP/telegram_worker.session-wal" "$APP/telegram_worker.session-shm"
chown tgjob:tgjob "$APP/telegram_worker.session" 2>/dev/null || true
systemctl start tg-job-agent.service
sleep 5
echo '--- STATUS ---'
systemctl is-active tg-job-agent.service || true
echo '--- TELEGRAMCLIENT ---'
grep -n 'TelegramClient' "$W" | head -10 || true
echo '--- JOURNAL ---'
journalctl -u tg-job-agent.service --since '2 minutes ago' --no-pager | tail -80 || true
echo '=== FIX COMPLETE ==='
