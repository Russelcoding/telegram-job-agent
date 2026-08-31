#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
echo '=== LIVE WORKER OBSERVE ==='
date -u
mkdir -p /etc/systemd/system/tg-job-agent.service.d
cat >/etc/systemd/system/tg-job-agent.service.d/unbuffered.conf <<'EOF'
[Service]
Environment=PYTHONUNBUFFERED=1
EOF
systemctl daemon-reload
systemctl restart tg-job-agent.service
sleep 25
echo '--- SERVICE ---'
systemctl is-active tg-job-agent.service || true
echo '--- JOURNAL ---'
journalctl -u tg-job-agent.service --since '3 minutes ago' --no-pager | tail -120 || true
echo '--- QUEUE COUNTS ---'
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'; c=sqlite3.connect(p,timeout=20); c.row_factory=sqlite3.Row
for t in ('send_queue','applications','contacts'):
 try:
  print(t, c.execute(f'select count(*) n from "{t}"').fetchone()['n'])
 except Exception as e: print(t,'ERR',repr(e))
try:
 print('queue_status', [tuple(r) for r in c.execute('select status,count(*) from send_queue group by status')])
 print('latest_queue')
 for r in c.execute('select id,recipient,status,telegram_message_id,error,created_at,processed_at from send_queue order by id desc limit 20'):
  print(tuple(r))
except Exception as e: print('queue detail ERR',repr(e))
try:
 print('app_status', [tuple(r) for r in c.execute('select status,count(*) from applications group by status')])
except Exception as e: print('app status ERR',repr(e))
c.close()
PY
echo '=== END LIVE WORKER OBSERVE ==='
