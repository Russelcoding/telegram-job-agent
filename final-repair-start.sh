#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/tg-agent-final-backup-$STAMP.tgz
REPORT=/root/tg-agent-final-$STAMP.txt
exec > >(tee "$REPORT") 2>&1

echo '=== FINAL REPAIR / SAFE START ==='
date -u

# Stop everything before patching.
systemctl stop tg-job-agent.service 2>/dev/null || true
systemctl stop tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer 2>/dev/null || true
systemctl stop tg-job-discovery.service tg-job-scanner.service tg-job-selector.service 2>/dev/null || true
pkill -f '/opt/tg-job-agent/worker.py' 2>/dev/null || true
sleep 2

tar --exclude='*.session-journal' -czf "$BACKUP" "$APP"
echo "backup=$BACKUP"

# Clean stale sqlite sidecars only while processes are stopped.
rm -f "$APP"/telegram.session-journal "$APP"/telegram.session-wal "$APP"/telegram.session-shm 2>/dev/null || true

# Prepare independent authorized Telethon sessions per process role.
for role in worker scanner discovery; do
  cp -f "$APP/telegram.session" "$APP/telegram_${role}.session"
  chown tgjob:tgjob "$APP/telegram_${role}.session" 2>/dev/null || true
  chmod 600 "$APP/telegram_${role}.session" 2>/dev/null || true
done

# Patch TelegramClient first positional string session argument to role-specific files.
python3 - <<'PY'
from pathlib import Path
import ast
APP=Path('/opt/tg-job-agent')
roles={'worker.py':'telegram_worker','scanner.py':'telegram_scanner','discover_sources.py':'telegram_discovery'}
for fn,new in roles.items():
    p=APP/fn
    src=p.read_text()
    tree=ast.parse(src)
    edits=[]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f=node.func
            name=(f.id if isinstance(f,ast.Name) else f.attr if isinstance(f,ast.Attribute) else '')
            if name=='TelegramClient' and node.args:
                a=node.args[0]
                if isinstance(a,ast.Constant) and isinstance(a.value,str):
                    edits.append((a.lineno,a.col_offset,a.end_lineno,a.end_col_offset,repr(new)))
    if not edits:
        # Safe fallback for the common literal form; otherwise leave untouched and report.
        old=src
        for q in ("'telegram'",'"telegram"',"'/opt/tg-job-agent/telegram'",'"/opt/tg-job-agent/telegram"'):
            pos=src.find('TelegramClient('+q)
            if pos!=-1:
                src=src.replace('TelegramClient('+q,'TelegramClient('+repr(new),1)
                break
        if src==old:
            print(fn,'session_patch=not_needed_or_dynamic')
            continue
    else:
        lines=src.splitlines(keepends=True)
        # Apply only single-line string literal edits, which is how Telethon sessions are normally declared.
        for ln,c0,eln,c1,repl in sorted(edits, reverse=True):
            if ln!=eln: continue
            i=ln-1
            lines[i]=lines[i][:c0]+repl+lines[i][c1:]
        src=''.join(lines)
    p.write_text(src)
    print(fn,'session_patch=ok',new)
PY

# Validate Python syntax before any start.
for f in worker.py scanner.py discover_sources.py selector.py; do
  "$APP/venv/bin/python" -m py_compile "$APP/$f"
done

# Preserve and clear pre-repair queue so no stale row can be sent.
python3 - <<'PY'
import sqlite3, time
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p, timeout=20)
print('db_integrity=',c.execute('pragma integrity_check').fetchone()[0])
# Keep a timestamped archival copy of current send_queue when present.
try:
    cols=[r[1] for r in c.execute('pragma table_info(send_queue)')]
    n=c.execute('select count(*) from send_queue').fetchone()[0]
    print('send_queue_before=',n,'cols=',cols)
    suffix=str(int(time.time()))
    c.execute(f'create table send_queue_pre_repair_{suffix} as select * from send_queue')
    c.execute('delete from send_queue')
    print('send_queue_cleared=1')
except Exception as e:
    print('send_queue_clear_skipped=',repr(e))
# Strengthen contact-level dedupe when an obvious recipient column exists.
try:
    cols=[r[1] for r in c.execute('pragma table_info(contacts)')]
    cand=next((x for x in cols if x.lower() in ('recipient','username','telegram_username','contact','peer','handle')),None)
    if cand:
        # Remove exact duplicate contact rows conservatively, preserving the oldest rowid.
        c.execute(f'''delete from contacts where rowid not in (
            select min(rowid) from contacts where "{cand}" is not null and trim("{cand}")<>'' group by lower(trim("{cand}"))
        ) and "{cand}" is not null and trim("{cand}")<>'' ''')
        c.execute(f'create unique index if not exists uq_contacts_recipient_norm on contacts(lower(trim("{cand}")))')
        print('contact_unique_index=ok',cand)
    else:
        print('contact_unique_index=no_candidate')
except Exception as e:
    print('contact_unique_index_warn=',repr(e))
c.commit(); c.close()
PY

# Run the whole intake chain sequentially while outbound worker is still stopped.
for s in tg-job-discovery.service tg-job-scanner.service tg-job-selector.service; do
  echo "=== RUN $s ==="
  systemctl reset-failed "$s" 2>/dev/null || true
  timeout 240 systemctl start --wait "$s" || true
  systemctl status "$s" --no-pager -l || true
  journalctl -u "$s" -n 50 --no-pager || true
done

# Fail closed if session/db locking still occurs in recent intake logs.
if journalctl -u tg-job-discovery.service -u tg-job-scanner.service -n 120 --no-pager | grep -qiE 'database is locked|OperationalError'; then
  echo 'FATAL: lock error still present; worker NOT started'
  exit 20
fi

python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/tg-job-agent/telegram_jobs.db',timeout=20)
print('db_integrity_after=',c.execute('pragma integrity_check').fetchone()[0])
for t in ('sources','jobs','applications','contacts','send_queue'):
    try: print(t,c.execute(f'select count(*) from "{t}"').fetchone()[0])
    except Exception as e: print(t,'ERR',repr(e))
c.close()
PY

# Restart timers first, then worker.
systemctl daemon-reload
systemctl enable --now tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer
systemctl enable --now tg-job-agent.service
sleep 8

echo '=== ACTIVE STATE ==='
systemctl is-active tg-job-agent.service
systemctl is-active tg-job-discovery.timer
systemctl is-active tg-job-scanner.timer
systemctl is-active tg-job-selector.timer
journalctl -u tg-job-agent.service -n 60 --no-pager || true

# Fail closed if the worker itself immediately shows a lock/traceback.
if journalctl -u tg-job-agent.service -n 80 --no-pager | grep -qiE 'database is locked|Traceback'; then
  echo 'FATAL: worker startup error; stopping outbound worker'
  systemctl stop tg-job-agent.service
  exit 21
fi

echo "REPORT=$REPORT"
echo '=== FINAL REPAIR COMPLETE ==='
