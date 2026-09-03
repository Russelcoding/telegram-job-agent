#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="/root/tg-candidate-safety-$STAMP.tgz"

echo '=== CANDIDATE POST SAFETY FIX ==='
date -u

systemctl stop tg-job-agent.service 2>/dev/null || true
cp -a "$APP/worker.py" "/root/worker.py.$STAMP.candidate-safety.bak"
cp -a "$DB" "/root/telegram_jobs.db.$STAMP.candidate-safety.bak"
tar -czf "$BACKUP" "$APP/worker.py" "$DB" 2>/dev/null || true

python3 - <<'PY'
from pathlib import Path
import ast, re
p=Path('/opt/tg-job-agent/worker.py')
s=p.read_text()

helper=r'''

# --- candidate/self-ad safety gate ---
def _candidate_post_reason(text):
    """Return a rejection reason when a source post is clearly a job-seeker CV/self-ad."""
    t=(text or '').lower().replace('ё','е')
    # Strong explicit candidate/self-search markers. These should never be treated as vacancies.
    strong=[
        '#резюме','#resume','#cv','#opentowork','#open_to_work','open to work',
        'ищу работу','ищу работу в','ищу вакансию','в поиске работы','нахожусь в поиске',
        'рассматриваю вакансии','рассматриваю предложения','рассматриваю позиции',
        'ищу новую работу','готов рассмотреть предложения','готова рассмотреть предложения',
        'looking for a job','looking for work','seeking a job','seeking opportunities',
        'seeking new opportunities','looking for new opportunities','available for work',
        'my resume','my cv','резюме:', 'мое резюме','моё резюме',
        'зарплатные ожидания','salary expectations','expected salary',
        'желаемая зарплата','желаемая должность','desired position','desired salary',
    ]
    for x in strong:
        if x in t:
            return 'candidate_marker:' + x

    # Candidate profile patterns: first-person experience + desired format/role/contact.
    candidate_profile=[
        r'\bопыт работы\b.{0,100}\b(?:лет|года|год)\b',
        r'\b(?:мой|мои)\s+опыт\b',
        r'\bi have\s+\d+\+?\s+years?\s+of\s+experience\b',
        r'\bexperience\s*:\s*\d+\+?\s*years?\b',
        r'\bготов(?:а)?\s+к\s+(?:работе|релокации|переезду)\b',
        r'\bпредпочитаемый\s+формат\b',
        r'\bформат\s+(?:работы\s*)?:\s*(?:удален|удалён|гибрид|офис)',
    ]
    hits=sum(bool(re.search(p,t,re.S)) for p in candidate_profile)
    # Require at least two profile-style signals to avoid rejecting normal vacancy descriptions.
    if hits >= 2:
        return 'candidate_profile'
    return None


def _queue_row_job_text(con, row):
    """Build source vacancy text from the queue row's referenced job, schema-tolerantly."""
    try:
        keys=row.keys() if hasattr(row,'keys') else []
        job_id=row['job_id'] if 'job_id' in keys else None
    except Exception:
        job_id=None
    if job_id is None:
        return ''
    try:
        info=con.execute('PRAGMA table_info(jobs)').fetchall()
        cols=[r[1] for r in info]
        if not cols:
            return ''
        # Prefer the canonical id/job_id column but tolerate schema drift.
        idcol='id' if 'id' in cols else ('job_id' if 'job_id' in cols else cols[0])
        jr=con.execute(f'SELECT * FROM jobs WHERE "{idcol}"=? LIMIT 1',(job_id,)).fetchone()
        if not jr:
            return ''
        if hasattr(jr,'keys'):
            vals=[jr[k] for k in jr.keys()]
        else:
            vals=list(jr)
        return '\n'.join(str(v) for v in vals if isinstance(v,str) and v.strip())
    except Exception:
        return ''


def _reject_candidate_queue_row(con, row):
    text=_queue_row_job_text(con,row)
    reason=_candidate_post_reason(text)
    if not reason:
        return False
    try:
        keys=row.keys() if hasattr(row,'keys') else []
        qid=row['id'] if 'id' in keys else None
        if qid is not None:
            # Preserve the row for audit; do not delete it.
            try:
                con.execute("UPDATE send_queue SET status='rejected_candidate', error=? WHERE id=?",(reason,qid))
            except Exception:
                con.execute("UPDATE send_queue SET status='rejected_candidate' WHERE id=?",(qid,))
            con.commit()
        print('SKIP_CANDIDATE_POST', qid, reason, flush=True)
    except Exception as e:
        # Fail closed: if we positively identified a candidate post, never send it even if audit update fails.
        print('SKIP_CANDIDATE_POST_AUDIT_ERROR', repr(e), flush=True)
    return True
# --- end candidate/self-ad safety gate ---
'''

if '_candidate_post_reason' not in s:
    # Put helper after imports, before runtime code.
    pos=0
    for m in re.finditer(r'^(?:from\s+\S+\s+import\s+.*|import\s+.*)\n',s,re.M):
        pos=m.end()
    s=s[:pos]+helper+s[pos:]

# Hard outbound-edge gate. We intentionally place it immediately before Telegram entity resolution/send path.
if 'if _reject_candidate_queue_row(con, row):' not in s:
    target=re.search(r'(?m)^(\s*)entity\s*=\s*await\s+client\.get_entity\(recipient\)\s*$',s)
    if not target:
        raise SystemExit('SAFETY ABORT: worker get_entity(recipient) line not found; no changes started')
    indent=target.group(1)
    inject=(f"{indent}if _reject_candidate_queue_row(con, row):\n"
            f"{indent}    continue\n")
    s=s[:target.start()]+inject+s[target.start():]

ast.parse(s)
p.write_text(s)
print('worker.py candidate safety gate patched')
PY

# Syntax must be valid before touching queue state or restarting outbound worker.
"$APP/venv/bin/python" -m py_compile "$APP/worker.py"

# Clean already-queued candidate/self-ad rows before worker resumes.
python3 - <<'PY'
import sqlite3,re
p='/opt/tg-job-agent/telegram_jobs.db'
c=sqlite3.connect(p,timeout=20); c.row_factory=sqlite3.Row

def reason(text):
    t=(text or '').lower().replace('ё','е')
    strong=['#резюме','#resume','#cv','#opentowork','#open_to_work','open to work','ищу работу','ищу вакансию','в поиске работы','нахожусь в поиске','рассматриваю вакансии','рассматриваю предложения','рассматриваю позиции','ищу новую работу','готов рассмотреть предложения','готова рассмотреть предложения','looking for a job','looking for work','seeking a job','seeking opportunities','seeking new opportunities','looking for new opportunities','available for work','my resume','my cv','резюме:','мое резюме','моё резюме','зарплатные ожидания','salary expectations','expected salary','желаемая зарплата','желаемая должность','desired position','desired salary']
    return next((x for x in strong if x in t),None)

job_cols=[r[1] for r in c.execute('pragma table_info(jobs)')]
idcol='id' if 'id' in job_cols else ('job_id' if 'job_id' in job_cols else (job_cols[0] if job_cols else None))
rejected=[]
if idcol:
    for q in c.execute("select * from send_queue where lower(coalesce(status,'')) in ('pending','approved','queued','held_floodwait')").fetchall():
        if 'job_id' not in q.keys() or q['job_id'] is None: continue
        j=c.execute(f'SELECT * FROM jobs WHERE "{idcol}"=? LIMIT 1',(q['job_id'],)).fetchone()
        if not j: continue
        text='\n'.join(str(j[k]) for k in j.keys() if isinstance(j[k],str) and j[k].strip())
        r=reason(text)
        if not r: continue
        try:
            c.execute("update send_queue set status='rejected_candidate', error=? where id=?",('candidate_marker:'+r,q['id']))
        except Exception:
            c.execute("update send_queue set status='rejected_candidate' where id=?",(q['id'],))
        rejected.append((q['id'],q['job_id'],r))
c.commit()
print('preexisting_candidate_rows_rejected=',len(rejected))
for x in rejected[:30]: print('REJECTED',x)
print('queue_status=',list(c.execute('select status,count(*) from send_queue group by status order by status')))
print('integrity=',c.execute('pragma integrity_check').fetchone()[0])
c.close()
PY

systemctl restart tg-job-agent.service
sleep 4

if ! systemctl is-active --quiet tg-job-agent.service; then
  echo 'FATAL: worker did not restart; restoring worker backup'
  cp -a "/root/worker.py.$STAMP.candidate-safety.bak" "$APP/worker.py"
  systemctl restart tg-job-agent.service || true
  exit 20
fi

if journalctl -u tg-job-agent.service --since '5 minutes ago' --no-pager | grep -q 'SyntaxError\|Traceback'; then
  echo 'FATAL: worker startup error; restoring worker backup'
  cp -a "/root/worker.py.$STAMP.candidate-safety.bak" "$APP/worker.py"
  "$APP/venv/bin/python" -m py_compile "$APP/worker.py" || true
  systemctl restart tg-job-agent.service || true
  exit 21
fi

echo '--- STATUS ---'
systemctl is-active tg-job-agent.service || true
journalctl -u tg-job-agent.service --since '5 minutes ago' --no-pager | grep -E 'READY|SENT|SKIP_CANDIDATE_POST|ERROR|Traceback' | tail -40 || true
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/tg-job-agent/telegram_jobs.db',timeout=20)
print('queue=',list(c.execute('select status,count(*) from send_queue group by status order by status')))
print('integrity=',c.execute('pragma integrity_check').fetchone()[0])
c.close()
PY

echo "backup=$BACKUP"
echo '=== CANDIDATE POST SAFETY FIX COMPLETE ==='
