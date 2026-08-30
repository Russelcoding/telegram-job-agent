#!/usr/bin/env bash
set -u
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
echo '=== ZERO-SEND LIVE DIAGNOSTIC ==='
date -u
echo '--- ACTIVE ---'
for u in tg-job-agent.service tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer tg-job-seeker-poster.timer; do printf '%-32s ' "$u"; systemctl is-active "$u" 2>/dev/null || true; done
echo '--- DB COUNTS / QUEUE ---'
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'; c=sqlite3.connect(p,timeout=20); c.row_factory=sqlite3.Row
for t in ('sources','jobs','applications','contacts','send_queue','seeker_group_posts'):
 try: print(t, c.execute(f'select count(*) n from "{t}"').fetchone()['n'])
 except Exception as e: print(t,'ERR',type(e).__name__)
for t in ('send_queue','applications','jobs','settings'):
 try:
  cols=[r[1] for r in c.execute(f'pragma table_info("{t}")')]
  print('\n',t,'cols=',','.join(cols))
  if t=='send_queue':
   sc=next((x for x in cols if x.lower()=='status'),None)
   if sc:
    print('queue_status=',[tuple(r) for r in c.execute(f'select "{sc}",count(*) from "{t}" group by "{sc}"')])
  if t=='applications':
   sc=next((x for x in cols if x.lower()=='status'),None)
   if sc: print('app_status=',[tuple(r) for r in c.execute(f'select "{sc}",count(*) from "{t}" group by "{sc}"')])
 except Exception as e: print(t,'ERR',repr(e))
c.close()
PY
echo '--- LATEST SELECTOR ---'
journalctl -u tg-job-selector.service --since '90 minutes ago' --no-pager 2>/dev/null | grep -E 'checked:|queued:|held_time:|rejected:|blocked_contact:|pending_queue:|integrity:|Traceback|ERROR|locked' | tail -35 || true
echo '--- LATEST SCANNER ---'
journalctl -u tg-job-scanner.service --since '90 minutes ago' --no-pager 2>/dev/null | grep -E 'SCANNER|scanned:|found:|added:|jobs|integrity:|Traceback|ERROR|locked' | tail -30 || true
echo '--- LATEST WORKER ---'
journalctl -u tg-job-agent.service --since '90 minutes ago' --no-pager 2>/dev/null | grep -E 'READY|SENT|ERROR|INVALID|FLOOD|Traceback|locked|QUEUE|SKIP|BLOCK' | tail -35 || true
echo '--- SEEKER POSTER ---'
journalctl -u tg-job-seeker-poster.service --since '90 minutes ago' --no-pager 2>/dev/null | grep -E 'SEEKER|Traceback|ERROR|FLOOD|locked' | tail -30 || true
echo '--- SELECTOR TIME/WINDOW CODE ---'
grep -nEi '07:30|19:00|window|timezone|time_zone|held_time|local.*time|country|utc|offset|zoneinfo' "$APP/selector.py" 2>/dev/null | head -80 || true
echo '=== END ==='
