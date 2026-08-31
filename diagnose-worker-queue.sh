#!/usr/bin/env bash
set -u
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
echo '=== WORKER QUEUE DIAGNOSTIC ==='
date -u
systemctl is-active tg-job-agent.service || true
echo '--- QUEUE ROWS ---'
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p,timeout=20); c.row_factory=sqlite3.Row
for t in ('send_queue','applications','contacts'):
 try:
  cols=[r[1] for r in c.execute(f'pragma table_info("{t}")')]
  print('\nTABLE',t,'COLS',cols)
  rows=c.execute(f'select * from "{t}" order by rowid desc limit 20').fetchall()
  for r in rows:
   d=dict(r)
   for k in list(d):
    if k.lower() in ('message','reply_text') and d[k]: d[k]=str(d[k])[:100].replace('\n',' ')
   print(d)
 except Exception as e: print(t,'ERR',repr(e))
c.close()
PY
echo '--- WORKER CODE QUEUE/DB ---'
grep -nEi 'send_queue|telegram_jobs|sqlite|pending|status|recipient|send_message|send_file|sleep|while' "$APP/worker.py" | head -180 || true
echo '--- WORKER JOURNAL 30 MIN ---'
journalctl -u tg-job-agent.service --since '30 minutes ago' --no-pager | tail -160 || true
echo '--- PROCESS ---'
ps -ef | grep '[w]orker.py' || true
echo '=== END WORKER QUEUE DIAGNOSTIC ==='
