#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
W=$APP/worker.py
DB=$APP/telegram_jobs.db
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo '=== WORKER FLOODWAIT QUEUE FIX ==='
cp -a "$W" "/root/worker.py.$STAMP.bak"
cp -a "$DB" "/root/telegram_jobs.db.$STAMP.bak"
systemctl stop tg-job-agent.service
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/tg-job-agent/worker.py')
s=p.read_text()
# Ensure FloodWait is imported.
if 'FloodWaitError' not in s:
    s=s.replace('from telethon.errors import ', 'from telethon.errors import FloodWaitError, ', 1) if 'from telethon.errors import ' in s else s.replace('from telethon import TelegramClient', 'from telethon import TelegramClient\nfrom telethon.errors import FloodWaitError',1)
# Worker must not turn a Telegram FloodWait into permanent recipient failure.
# Insert a dedicated handler before generic Exception handlers where possible.
needle='except Exception as e:'
if 'WORKER_FLOODWAIT' not in s and needle in s:
    repl="except FloodWaitError as e:\n            print('WORKER_FLOODWAIT', getattr(e, 'seconds', None), flush=True)\n            await asyncio.sleep(min(int(getattr(e, 'seconds', 60)), 300))\n            continue\n        "+needle
    s=s.replace(needle,repl,1)
p.write_text(s)
print('worker patched')
PY
python3 -m py_compile "$W"
# Requeue only recent rows whose failure text is clearly FloodWait/ResolveUsername related.
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'; c=sqlite3.connect(p)
cols=[r[1] for r in c.execute('pragma table_info(send_queue)')]
if 'status' in cols and 'error' in cols:
 cur=c.execute("UPDATE send_queue SET status='pending', error=NULL, processed_at=NULL WHERE lower(coalesce(status,''))='failed' AND (lower(coalesce(error,'')) LIKE '%floodwait%' OR lower(coalesce(error,'')) LIKE '%wait of %seconds%' OR lower(coalesce(error,'')) LIKE '%resolveusername%')")
 print('requeued_floodwait_rows',cur.rowcount)
c.commit()
print('queue_status', list(c.execute("select status,count(*) from send_queue group by status")))
c.close()
PY
systemctl start tg-job-agent.service
sleep 8
echo '--- WORKER ---'; systemctl is-active tg-job-agent.service || true
echo '--- RECENT JOURNAL ---'; journalctl -u tg-job-agent.service --since '2 minutes ago' --no-pager | tail -100 || true
echo '--- QUEUE STATUS ---'
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/tg-job-agent/telegram_jobs.db')
print(list(c.execute("select status,count(*) from send_queue group by status")))
c.close()
PY
echo '=== FIX COMPLETE ==='
