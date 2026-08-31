#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB=$APP/telegram_jobs.db
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo '=== SAFE FLOODWAIT HOLD ==='
cp -a "$DB" "/root/telegram_jobs.db.$STAMP.bak"
# Do not edit worker.py. Preserve successful sends. Convert only clearly FloodWait-caused failures to held_floodwait.
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p, timeout=30)
c.execute('pragma busy_timeout=30000')
cols=[r[1] for r in c.execute('pragma table_info(send_queue)')]
print('columns',cols)
if 'status' in cols and 'error' in cols:
    q="""UPDATE send_queue SET status='held_floodwait'
         WHERE lower(coalesce(status,''))='failed'
           AND (lower(coalesce(error,'')) LIKE '%wait of %seconds%'
             OR lower(coalesce(error,'')) LIKE '%floodwait%'
             OR lower(coalesce(error,'')) LIKE '%resolveusername%')"""
    cur=c.execute(q); print('held_floodwait',cur.rowcount)
c.commit()
print('queue',list(c.execute('select status,count(*) from send_queue group by status order by status')))
print('integrity',c.execute('pragma integrity_check').fetchone()[0])
c.close()
PY
# Keep scanner disabled during its already-known Telegram cooldown. Worker stays running for known/cached entities.
echo 'worker='$(systemctl is-active tg-job-agent.service || true)
echo 'scanner_timer='$(systemctl is-active tg-job-scanner.timer || true)
echo 'selector_timer='$(systemctl is-active tg-job-selector.timer || true)
echo 'discovery_timer='$(systemctl is-active tg-job-discovery.timer || true)
echo '--- recent worker ---'
journalctl -u tg-job-agent.service --since '5 minutes ago' --no-pager | tail -80 || true
echo '=== SAFE HOLD COMPLETE ==='
