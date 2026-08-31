#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo '=== GLOBAL FLOW + FLOODWAIT MITIGATION ==='
date -u

systemctl stop tg-job-scanner.timer tg-job-scanner.service tg-job-selector.timer tg-job-selector.service 2>/dev/null || true
cp "$APP/scanner.py" "/root/scanner.py.$STAMP.bak"
cp "$APP/selector.py" "/root/selector.py.$STAMP.bak"
cp "$DB" "/root/telegram_jobs.db.$STAMP.bak"

python3 - <<'PY'
from pathlib import Path
import ast,re
app=Path('/opt/tg-job-agent')

# 1) Selector: preserve employer-local window, but make unknown/missing timezone fail open
p=app/'selector.py'; s=p.read_text()
# replace function allowed_now conservatively by locating def and next top-level def
m=re.search(r'^def allowed_now\(country, source_timezone=None\):\n', s, re.M)
if not m:
    raise SystemExit('selector allowed_now() not found')
start=m.start(); body_start=m.end()
nextm=re.search(r'^def \w+\(', s[body_start:], re.M)
end=body_start+(nextm.start() if nextm else len(s)-body_start)
new='''def allowed_now(country, source_timezone=None):\n    tzname = (source_timezone or '').strip()\n    if not tzname and country:\n        tzname = COUNTRY_TZ.get((country or '').strip().lower())\n    # Unknown timezone must NOT globally block the pipeline.\n    # Known employer timezone still respects 07:30-19:00 local time.\n    if not tzname:\n        local = datetime.now(timezone.utc)\n        return True, '', local.strftime("%Y-%m-%d %H:%M")\n    try:\n        local = datetime.now(timezone.utc).astimezone(ZoneInfo(tzname))\n    except Exception:\n        local = datetime.now(timezone.utc)\n        return True, tzname, local.strftime("%Y-%m-%d %H:%M")\n    mins = local.hour * 60 + local.minute\n    allowed = (7 * 60 + 30) <= mins < (19 * 60)\n    return allowed, tzname, local.strftime("%Y-%m-%d %H:%M")\n\n'''
s=s[:start]+new+s[end:]
p.write_text(s)
ast.parse(s)
print('selector.py patched: employer-local 07:30-19:00, unknown tz fail-open')

# 2) Scanner: add persistent source-resolution cooldown so bad/unresolvable usernames
# are not hammered every cycle. We do not bypass Telegram FloodWait.
p=app/'scanner.py'; s=p.read_text()
# Add imports if missing
if 'import time' not in s:
    s='import time\n'+s
# We use a tiny local sqlite cache independent from Telethon sessions.
helper='''\n# --- persistent resolve guard injected by repair ---\n_RESOLVE_GUARD_DB = "/opt/tg-job-agent/resolve_guard.db"\ndef _rg_conn():\n    import sqlite3\n    c=sqlite3.connect(_RESOLVE_GUARD_DB, timeout=10)\n    c.execute("CREATE TABLE IF NOT EXISTS source_guard (key TEXT PRIMARY KEY, next_try REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, note TEXT)")\n    c.commit(); return c\ndef _rg_allowed(key):\n    try:\n        c=_rg_conn(); r=c.execute("SELECT next_try FROM source_guard WHERE key=?",(str(key),)).fetchone(); c.close()\n        return not r or float(r[0] or 0) <= time.time()\n    except Exception:\n        return True\ndef _rg_fail(key, seconds, note=''):\n    try:\n        c=_rg_conn(); c.execute("INSERT INTO source_guard(key,next_try,failures,note) VALUES(?,?,1,?) ON CONFLICT(key) DO UPDATE SET next_try=excluded.next_try, failures=source_guard.failures+1, note=excluded.note",(str(key),time.time()+int(seconds),str(note)[:300])); c.commit(); c.close()\n    except Exception: pass\ndef _rg_ok(key):\n    try:\n        c=_rg_conn(); c.execute("DELETE FROM source_guard WHERE key=?",(str(key),)); c.commit(); c.close()\n    except Exception: pass\n# --- end resolve guard ---\n'''
if '_RESOLVE_GUARD_DB' not in s:
    # place after imports before first class/def where possible
    pos=0
    for mm in re.finditer(r'^(?:from\s+\S+\s+import\s+.*|import\s+.*)\n',s,re.M): pos=mm.end()
    s=s[:pos]+helper+s[pos:]

# Patch common "for source in sources" loops by adding guard at loop top.
# This is deliberately conservative and idempotent.
if '_rg_allowed(' not in s[s.find('async def'):]:
    patterns=[r'(?m)^(\s*)for\s+source\s+in\s+sources\s*:\s*$', r'(?m)^(\s*)for\s+row\s+in\s+sources\s*:\s*$']
    patched=False
    for pat in patterns:
        mm=re.search(pat,s)
        if mm:
            indent=mm.group(1); var='source' if 'source' in mm.group(0).split('for',1)[1].split('in',1)[0] else 'row'
            # infer a stable key without assuming schema
            inject=f"\n{indent}    _guard_key = ({var}.get('username') if hasattr({var}, 'get') else None) or ({var}.get('source_key') if hasattr({var}, 'get') else None) or ({var}.get('id') if hasattr({var}, 'get') else None) or str({var})\n{indent}    if not _rg_allowed(_guard_key):\n{indent}        continue"
            s=s[:mm.end()]+inject+s[mm.end():]
            patched=True
            break
    print('scanner loop guard:', 'patched' if patched else 'loop-shape-not-found; floodwait timer will be paused')

p.write_text(s)
ast.parse(s)
PY

# Never keep hammering Telegram during an active FloodWait. If recent scanner log has one,
# pause scanner timer for 23h using a transient systemd timer, while selector+worker keep running.
WAIT=$(journalctl -u tg-job-scanner.service --since '2 hours ago' --no-pager 2>/dev/null | sed -nE 's/.*wait of ([0-9]+) seconds.*/\1/p' | tail -1 || true)
if [[ -n "${WAIT:-}" && "$WAIT" =~ ^[0-9]+$ && "$WAIT" -gt 0 ]]; then
  echo "active Telegram FloodWait=${WAIT}s; scanner timer will remain stopped until cooldown expires"
  systemctl stop tg-job-scanner.timer 2>/dev/null || true
  systemd-run --unit=tg-job-scanner-resume --on-active="${WAIT}s" /bin/systemctl start tg-job-scanner.timer >/dev/null
else
  systemctl start tg-job-scanner.timer
fi

# Re-run selector now; worker remains available to consume anything legitimately sendable.
systemctl start tg-job-selector.timer
systemctl start tg-job-selector.service || true
systemctl restart tg-job-agent.service
sleep 3

echo '--- SELECTOR RESULT ---'
journalctl -u tg-job-selector.service --since '5 minutes ago' --no-pager | grep -E 'checked:|queued:|held_time:|rejected:|blocked_contact:|pending_queue:|integrity:|Traceback|ERROR' | tail -20 || true

echo '--- WORKER RESULT ---'
journalctl -u tg-job-agent.service --since '5 minutes ago' --no-pager | grep -E 'READY|SENT|ERROR|INVALID|FLOOD|Traceback|locked|QUEUE|SKIP|BLOCK' | tail -30 || true

echo '--- STATUS ---'
for u in tg-job-agent.service tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer; do printf '%-30s ' "$u"; systemctl is-active "$u" 2>/dev/null || true; done
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'; c=sqlite3.connect(p,timeout=20)
for t in ('jobs','send_queue','applications','contacts'):
 try: print(t,c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
 except Exception as e: print(t,'ERR',e)
try: print('integrity',c.execute('pragma integrity_check').fetchone()[0])
except Exception as e: print('integrity ERR',e)
c.close()
PY

echo "BACKUPS=/root/scanner.py.$STAMP.bak /root/selector.py.$STAMP.bak /root/telegram_jobs.db.$STAMP.bak"
echo '=== GLOBAL FLOW FIX COMPLETE ==='
