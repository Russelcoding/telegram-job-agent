#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB=$APP/telegram_jobs.db
echo '=== INSTALL AUTO FLOODWAIT RESUME ==='
cat >/usr/local/sbin/tg-floodwait-resume.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DB=/opt/tg-job-agent/telegram_jobs.db
# Re-enable normal scanner schedule.
systemctl enable --now tg-job-scanner.timer
# Return only our explicit FloodWait holds to the normal queue.
python3 - <<'PY'
import sqlite3
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p,timeout=30); c.execute('pragma busy_timeout=30000')
cur=c.execute("UPDATE send_queue SET status='pending', error=NULL, processed_at=NULL WHERE status='held_floodwait'")
print('released',cur.rowcount)
c.commit(); print('queue',list(c.execute('select status,count(*) from send_queue group by status'))); c.close()
PY
systemctl restart tg-job-agent.service
systemctl start tg-job-scanner.service || true
systemctl start tg-job-selector.service || true
EOF
chmod 755 /usr/local/sbin/tg-floodwait-resume.sh
cat >/etc/systemd/system/tg-floodwait-resume.service <<'EOF'
[Unit]
Description=Resume Telegram job agent after FloodWait
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tg-floodwait-resume.sh
EOF
cat >/etc/systemd/system/tg-floodwait-resume.timer <<'EOF'
[Unit]
Description=One-time Telegram FloodWait recovery
[Timer]
OnActiveSec=21h
AccuracySec=5min
Persistent=true
Unit=tg-floodwait-resume.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now tg-floodwait-resume.timer
echo 'worker='$(systemctl is-active tg-job-agent.service || true)
echo 'resume_timer='$(systemctl is-active tg-floodwait-resume.timer || true)
systemctl list-timers tg-floodwait-resume.timer --no-pager || true
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/tg-job-agent/telegram_jobs.db'); print('queue',list(c.execute('select status,count(*) from send_queue group by status'))); c.close()
PY
echo '=== AUTO RESUME INSTALLED ==='
