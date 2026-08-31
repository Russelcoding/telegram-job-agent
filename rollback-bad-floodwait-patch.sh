#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
W=$APP/worker.py
echo '=== ROLLBACK MALFORMED FLOODWAIT PATCH ==='
systemctl stop tg-job-agent.service || true
BACKUP=$(ls -1t /root/worker.py.*.bak 2>/dev/null | head -1 || true)
if [ -z "$BACKUP" ]; then echo 'ERROR: worker backup not found'; exit 1; fi
echo "restore=$BACKUP"
cp -a "$BACKUP" "$W"
chown tgjob:tgjob "$W" || true
python3 -m py_compile "$W"
# Keep the known-good dedicated Telethon session configuration.
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/tg-job-agent/worker.py')
s=p.read_text()
s=s.replace('telegram_worker.session_worker.session','telegram_worker.session')
p.write_text(s)
PY
python3 -m py_compile "$W"
rm -f "$APP/telegram_worker.session-journal" "$APP/telegram_worker.session-wal" "$APP/telegram_worker.session-shm"
systemctl start tg-job-agent.service
sleep 6
echo '--- STATUS ---'
systemctl is-active tg-job-agent.service || true
echo '--- CLIENT ---'
grep -n 'TelegramClient' "$W" | head -8 || true
echo '--- JOURNAL ---'
journalctl -u tg-job-agent.service --since '2 minutes ago' --no-pager | tail -60 || true
echo '=== ROLLBACK COMPLETE ==='
