#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo '=== FIX TELETHON SESSION LOCK ==='
date -u

systemctl stop tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer 2>/dev/null || true
systemctl stop tg-job-discovery.service tg-job-scanner.service tg-job-selector.service 2>/dev/null || true
systemctl stop tg-job-agent.service 2>/dev/null || true
sleep 2

tar -czf "/root/tg-agent-before-session-fix-$STAMP.tgz" "$APP" 2>/dev/null || true

# Remove stale SQLite sidecars while every Telethon process is stopped.
rm -f "$APP"/telegram.session-journal "$APP"/telegram.session-wal "$APP"/telegram.session-shm
rm -f "$APP"/telegram_worker.session-journal "$APP"/telegram_worker.session-wal "$APP"/telegram_worker.session-shm
rm -f "$APP"/telegram_scanner.session-journal "$APP"/telegram_scanner.session-wal "$APP"/telegram_scanner.session-shm
rm -f "$APP"/telegram_discovery.session-journal "$APP"/telegram_discovery.session-wal "$APP"/telegram_discovery.session-shm

# Clone the already-authorized user session. Each concurrent process gets its own SQLite file.
cp -f "$APP/telegram.session" "$APP/telegram_worker.session"
cp -f "$APP/telegram.session" "$APP/telegram_scanner.session"
cp -f "$APP/telegram.session" "$APP/telegram_discovery.session"
chown tgjob:tgjob "$APP"/telegram*.session 2>/dev/null || true
chmod 600 "$APP"/telegram*.session 2>/dev/null || true

# Robustly rewrite every Telethon TelegramClient(...) call, including dynamic first args.
python3 - <<'PY'
import ast, pathlib, shutil, time
app=pathlib.Path('/opt/tg-job-agent')
roles={
 'worker.py': str(app/'telegram_worker'),
 'scanner.py': str(app/'telegram_scanner'),
 'discover_sources.py': str(app/'telegram_discovery'),
}
for name, session in roles.items():
    p=app/name
    src=p.read_text()
    shutil.copy2(p, str(p)+'.pre-session-fix')
    tree=ast.parse(src)
    changed=0
    class T(ast.NodeTransformer):
        def visit_Call(self,node):
            nonlocal_changed = None
            self.generic_visit(node)
            fn=node.func
            is_tc=(isinstance(fn,ast.Name) and fn.id=='TelegramClient') or (isinstance(fn,ast.Attribute) and fn.attr=='TelegramClient')
            if is_tc:
                if node.args:
                    node.args[0]=ast.Constant(session)
                else:
                    # Extremely defensive fallback if session is supplied as keyword.
                    found=False
                    for kw in node.keywords:
                        if kw.arg in ('session','session_name'):
                            kw.value=ast.Constant(session); found=True
                    if not found:
                        node.args.insert(0,ast.Constant(session))
                self.changed += 1
            return node
    t=T(); t.changed=0
    tree=t.visit(tree); ast.fix_missing_locations(tree)
    if t.changed < 1:
        raise SystemExit(f'ERROR: no TelegramClient call found in {name}')
    p.write_text(ast.unparse(tree)+'\n')
    print(f'{name}: patched TelegramClient calls={t.changed} session={session}.session')
PY

for f in worker.py scanner.py discover_sources.py selector.py; do
  "$APP/venv/bin/python" -m py_compile "$APP/$f"
done

echo '--- SESSION FILES ---'
ls -lh "$APP"/telegram*.session

echo '--- PREFLIGHT DISCOVERY ---'
timeout 180 "$APP/venv/bin/python" "$APP/discover_sources.py" || true

echo '--- PREFLIGHT SCANNER ---'
timeout 180 "$APP/venv/bin/python" "$APP/scanner.py" || true

echo '--- PREFLIGHT SELECTOR ---'
timeout 120 "$APP/venv/bin/python" "$APP/selector.py" || true

systemctl daemon-reload
systemctl enable --now tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer
systemctl restart tg-job-agent.service
sleep 5

echo '--- ACTIVE ---'
for u in tg-job-agent.service tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer; do printf '%-30s ' "$u"; systemctl is-active "$u" || true; done

echo '--- RECENT LOCK ERRORS ---'
if journalctl -u tg-job-agent.service -u tg-job-discovery.service -u tg-job-scanner.service --since '3 minutes ago' --no-pager | grep -Ei 'database is locked|OperationalError|Traceback'; then
  echo 'LOCK_ERROR_DETECTED'
else
  echo 'NO_RECENT_SESSION_LOCK_ERRORS'
fi

echo '--- DB COUNTS ---'
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p,timeout=30)
for t in ('sources','jobs','send_queue','applications','contacts'):
    try: print(t,c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
    except Exception as e: print(t,'ERR',e)
print('integrity',c.execute('PRAGMA integrity_check').fetchone()[0])
c.close()
PY

echo "BACKUP=/root/tg-agent-before-session-fix-$STAMP.tgz"
echo '=== SESSION LOCK FIX COMPLETE ==='
