#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"

echo '=== STAGE2 TARGETED INSPECTION ==='
systemctl stop tg-job-agent.service tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer 2>/dev/null || true

python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p)
print('DB integrity:', c.execute('pragma integrity_check').fetchone()[0])
for t in ['send_queue','contacts','applications','jobs','sources','settings']:
    try:
        cols=c.execute(f'pragma table_info("{t}")').fetchall()
        print('\nTABLE',t,'ROWS',c.execute(f'select count(*) from "{t}"').fetchone()[0])
        print('COLUMNS',[(x[1],x[2],x[3],x[4],x[5]) for x in cols])
        if t in ('send_queue','contacts','settings'):
            names=[x[1] for x in cols]
            safe=[]
            for n in names:
                if any(s in n.lower() for s in ('token','hash','secret','password','api_')): continue
                safe.append(n)
            q=', '.join('"'+n+'"' for n in safe[:12])
            if q:
                for r in c.execute(f'select {q} from "{t}" order by rowid desc limit 8'):
                    print('ROW',r)
    except Exception as e:
        print('ERR',t,repr(e))
c.close()
PY

echo '\n=== TELETHON DECLARATIONS ==='
for f in worker.py scanner.py discover_sources.py selector.py; do
  echo "--- $f"
  grep -nE 'TelegramClient|telegram\.session|send_message|send_file|AIRTABLE|airtable|send_queue|contacts|applications|AUTO_SEND' "$APP/$f" 2>/dev/null | head -120 || true
done

echo '\n=== SYSTEMD UNITS ==='
for u in tg-job-agent.service tg-job-discovery.service tg-job-scanner.service tg-job-selector.service; do
  echo "--- $u"
  systemctl cat "$u" 2>/dev/null || true
done

echo '\n=== SAFETY ==='
echo 'All outbound/timers remain stopped.'
