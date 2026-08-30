#!/usr/bin/env bash
set -euo pipefail
APP=/opt/tg-job-agent
DB="$APP/telegram_jobs.db"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/tg-agent-schedule-backup-$STAMP.tgz
REPORT=/root/tg-agent-schedule-$STAMP.txt
exec > >(tee "$REPORT") 2>&1

echo '=== INTENSIVE SCHEDULE + SAFE SEEKER POSTS ==='
date -u

# Backup before changing units/code.
tar --exclude='*.session-journal' -czf "$BACKUP" "$APP"
echo "backup=$BACKUP"

# Staggered timers: discovery 30m, scanner 10m, selector 5m.
cat >/etc/systemd/system/tg-job-discovery.timer <<'EOF'
[Unit]
Description=Telegram job source discovery every 30 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
RandomizedDelaySec=20s
AccuracySec=10s
Persistent=true
Unit=tg-job-discovery.service

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/tg-job-scanner.timer <<'EOF'
[Unit]
Description=Telegram job scanner every 10 minutes

[Timer]
OnBootSec=4min
OnUnitActiveSec=10min
RandomizedDelaySec=20s
AccuracySec=10s
Persistent=true
Unit=tg-job-scanner.service

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/tg-job-selector.timer <<'EOF'
[Unit]
Description=Telegram job selector every 5 minutes

[Timer]
OnBootSec=6min
OnUnitActiveSec=5min
RandomizedDelaySec=15s
AccuracySec=10s
Persistent=true
Unit=tg-job-selector.service

[Install]
WantedBy=timers.target
EOF

# Dedicated authorized Telethon session for group posting, isolated from worker/scanner/discovery.
systemctl stop tg-job-seeker-poster.timer tg-job-seeker-poster.service 2>/dev/null || true
cp -f "$APP/telegram.session" "$APP/telegram_poster.session"
chown tgjob:tgjob "$APP/telegram_poster.session" 2>/dev/null || true
chmod 600 "$APP/telegram_poster.session" 2>/dev/null || true

cat >"$APP/seeker_poster.py" <<'PY'
#!/usr/bin/env python3
import os, re, sqlite3, time, random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError

APP=Path('/opt/tg-job-agent')
DB=APP/'telegram_jobs.db'
load_dotenv(APP/'.env')
API_ID=int(os.environ['TG_API_ID'])
API_HASH=os.environ['TG_API_HASH']
SESSION=str(APP/'telegram_poster')
COOLDOWN_DAYS=14
MAX_POSTS_PER_RUN=3

EN_MSG=(
    "Hello! I’m open to new opportunities in operations, project/operations management, "
    "business development, partnerships, sales/account management, logistics/supply chain, "
    "procurement, e-commerce, hospitality/property operations and related management roles. "
    "Open to international and remote opportunities. CV available on request. "
    "Please DM me if you know of a relevant role. Thank you!"
)
RU_MSG=(
    "Здравствуйте! Рассматриваю новые возможности в операционном и проектном управлении, "
    "business development, партнёрствах, продажах/account management, логистике и supply chain, "
    "закупках, e-commerce, hospitality/property operations и смежных управленческих ролях. "
    "Рассматриваю международные и удалённые позиции. Резюме отправлю по запросу. "
    "Буду благодарен за личное сообщение, если знаете подходящую вакансию."
)

POSITIVE=[
    r'job\s*seek', r'candidate', r'post\s+(?:your\s+)?(?:cv|resume)', r'cv\s+welcome',
    r'resume\s+welcome', r'looking\s+for\s+(?:a\s+)?job', r'vacanc(?:y|ies).{0,20}candidate',
    r'ищу\s+работ', r'соискател', r'резюме', r'кандидат', r'поиск\s+работ',
]
NEGATIVE=[
    r'vacanc(?:y|ies)\s+only', r'jobs?\s+only', r'no\s+self.?promo', r'no\s+ads',
    r'no\s+advertis', r'employers?\s+only', r'только\s+ваканс', r'без\s+реклам',
    r'реклама\s+запрещ', r'соискател.{0,15}запрещ', r'резюме.{0,15}запрещ'
]

def cols(conn, table):
    return [r[1] for r in conn.execute(f'pragma table_info("{table}")')]

def pick(candidates, available):
    low={x.lower():x for x in available}
    for c in candidates:
        if c in low: return low[c]
    return None

def norm_handle(v):
    if v is None: return None
    s=str(v).strip()
    if not s: return None
    if s.startswith('https://t.me/'): s=s.split('https://t.me/',1)[1].split('?',1)[0].strip('/')
    if s.startswith('t.me/'): s=s.split('t.me/',1)[1].split('?',1)[0].strip('/')
    if s.startswith('@'): return s
    if re.fullmatch(r'[A-Za-z0-9_]{5,}',s): return '@'+s
    try: return int(s)
    except: return None

def russianish(text):
    cyr=len(re.findall(r'[А-Яа-яЁё]', text or ''))
    lat=len(re.findall(r'[A-Za-z]', text or ''))
    return cyr > lat and cyr >= 10

def allowed_by_rules(text):
    t=(text or '').lower()
    if any(re.search(p,t,re.S) for p in NEGATIVE): return False
    return any(re.search(p,t,re.S) for p in POSITIVE)

async def main():
    conn=sqlite3.connect(DB, timeout=30)
    conn.execute('''create table if not exists seeker_group_posts(
        id integer primary key autoincrement,
        source_key text not null,
        group_title text,
        posted_at text not null,
        telegram_message_id integer,
        language text,
        status text not null,
        note text
    )''')
    conn.execute('create index if not exists ix_seeker_posts_key_time on seeker_group_posts(source_key,posted_at)')
    conn.commit()

    available=cols(conn,'sources')
    handle_col=pick(['username','handle','telegram_username','source_username','chat_username','url','link','source','chat_id','telegram_id','peer_id'], available)
    title_col=pick(['title','name','source_name','chat_title'], available)
    active_col=pick(['active','enabled','is_active','status'], available)
    if not handle_col:
        print('SEEKER_POSTER no usable source handle column; no-op')
        return

    q=f'SELECT rowid,* FROM sources ORDER BY rowid DESC'
    rows=conn.execute(q).fetchall()
    names=['rowid']+available
    candidates=[]
    for row in rows:
        d=dict(zip(names,row))
        if active_col:
            av=str(d.get(active_col,'')).strip().lower()
            if av in ('0','false','disabled','inactive','rejected','closed'): continue
        h=norm_handle(d.get(handle_col))
        if h is None: continue
        candidates.append((h, str(d.get(title_col) or '')))
    random.shuffle(candidates)

    cutoff=(datetime.now(timezone.utc)-timedelta(days=COOLDOWN_DAYS)).isoformat()
    posted=0
    client=TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    for handle,title_hint in candidates:
        if posted>=MAX_POSTS_PER_RUN: break
        key=str(handle).lower()
        if conn.execute('select 1 from seeker_group_posts where source_key=? and status="sent" and posted_at>=? limit 1',(key,cutoff)).fetchone():
            continue
        try:
            entity=await client.get_entity(handle)
            # Only groups/supergroups; never broadcast channels or private users.
            if isinstance(entity, Channel) and not getattr(entity,'megagroup',False):
                continue
            if not isinstance(entity,(Channel,Chat)):
                continue
            title=getattr(entity,'title',None) or title_hint or key
            about=''
            try:
                if isinstance(entity, Channel):
                    full=await client(GetFullChannelRequest(entity))
                    about=getattr(full.full_chat,'about','') or ''
            except Exception:
                pass
            recent=[]
            try:
                async for m in client.iter_messages(entity, limit=35):
                    if m.message: recent.append(m.message)
            except Exception:
                pass
            evidence='\n'.join([title,about]+recent[:35])[:40000]
            if not allowed_by_rules(evidence):
                continue
            msg=RU_MSG if russianish(evidence) else EN_MSG
            lang='ru' if msg==RU_MSG else 'en'
            sent=await client.send_message(entity,msg,link_preview=False)
            now=datetime.now(timezone.utc).isoformat()
            conn.execute('insert into seeker_group_posts(source_key,group_title,posted_at,telegram_message_id,language,status,note) values(?,?,?,?,?,?,?)',
                         (key,title,now,getattr(sent,'id',None),lang,'sent','rules evidence positive; no CV attached'))
            conn.commit()
            posted+=1
            print('SEEKER_POST_SENT',key,title,getattr(sent,'id',None),lang)
            await __import__('asyncio').sleep(random.uniform(12,28))
        except FloodWaitError as e:
            print('SEEKER_POSTER FLOOD_WAIT',getattr(e,'seconds',None),'stopping run')
            break
        except (ChatWriteForbiddenError,UserBannedInChannelError) as e:
            conn.execute('insert into seeker_group_posts(source_key,group_title,posted_at,telegram_message_id,language,status,note) values(?,?,?,?,?,?,?)',
                         (key,title_hint,datetime.now(timezone.utc).isoformat(),None,None,'blocked',type(e).__name__))
            conn.commit()
        except Exception as e:
            print('SEEKER_POST_SKIP',key,type(e).__name__,str(e)[:180])
    await client.disconnect()
    conn.close()
    print('SEEKER_POSTER_DONE posted=',posted,'cooldown_days=',COOLDOWN_DAYS,'max_per_run=',MAX_POSTS_PER_RUN)

if __name__=='__main__':
    import asyncio
    asyncio.run(main())
PY

chown tgjob:tgjob "$APP/seeker_poster.py" "$APP/telegram_poster.session" 2>/dev/null || true
chmod 750 "$APP/seeker_poster.py"

cat >/etc/systemd/system/tg-job-seeker-poster.service <<'EOF'
[Unit]
Description=Telegram job-seeker group poster
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=tgjob
WorkingDirectory=/opt/tg-job-agent
ExecStart=/opt/tg-job-agent/venv/bin/python /opt/tg-job-agent/seeker_poster.py
Nice=10
EOF

cat >/etc/systemd/system/tg-job-seeker-poster.timer <<'EOF'
[Unit]
Description=Run safe job-seeker group poster every 6 hours

[Timer]
OnBootSec=12min
OnUnitActiveSec=6h
RandomizedDelaySec=10min
Persistent=true
Unit=tg-job-seeker-poster.service

[Install]
WantedBy=timers.target
EOF

# Validate before activation.
"$APP/venv/bin/python" -m py_compile "$APP/seeker_poster.py"
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/tg-job-agent/telegram_jobs.db',timeout=30)
print('db_integrity=',c.execute('pragma integrity_check').fetchone()[0])
print('sources=',c.execute('select count(*) from sources').fetchone()[0])
c.close()
PY

systemctl daemon-reload
systemctl restart tg-job-discovery.timer tg-job-scanner.timer tg-job-selector.timer
systemctl enable --now tg-job-seeker-poster.timer
# Worker remains continuous; ensure it is up.
systemctl enable --now tg-job-agent.service

echo '=== TIMER SCHEDULE ==='
systemctl list-timers --all --no-pager | grep -E 'tg-job-(discovery|scanner|selector|seeker-poster)' || true
echo '=== WORKER ==='
systemctl is-active tg-job-agent.service
echo '=== COMPLETE ==='
echo "REPORT=$REPORT"
