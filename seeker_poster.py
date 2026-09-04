#!/usr/bin/env python3
import asyncio
import os
import random
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat

APP = Path('/opt/tg-job-agent')
DB = APP / 'telegram_jobs.db'
SESSION = str(APP / 'telegram_poster')
SESSION_DB = APP / 'telegram_poster.session'
load_dotenv(APP / '.env')
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']

COOLDOWN_DAYS = 14
MAX_POSTS_PER_RUN = 3
INTER_POST_MIN = 120
INTER_POST_MAX = 300

EN_MSG = (
    "Hello! I’m open to new opportunities in operations, project/operations management, "
    "business development, partnerships, sales/account management, logistics/supply chain, "
    "procurement, e-commerce, hospitality/property operations and related management roles. "
    "Open to international and remote opportunities. CV available on request. "
    "Please DM me if you know of a relevant role. Thank you!"
)
RU_MSG = (
    "Здравствуйте! Рассматриваю новые возможности в операционном и проектном управлении, "
    "business development, партнёрствах, продажах/account management, логистике и supply chain, "
    "закупках, e-commerce, hospitality/property operations и смежных управленческих ролях. "
    "Рассматриваю международные и удалённые позиции. Резюме отправлю по запросу. "
    "Буду благодарен за личное сообщение, если знаете подходящую вакансию."
)

POSITIVE = [
    r'job\s*seek', r'candidate', r'post\s+(?:your\s+)?(?:cv|resume)', r'cv\s+welcome',
    r'resume\s+welcome', r'looking\s+for\s+(?:a\s+)?job', r'vacanc(?:y|ies).{0,20}candidate',
    r'ищу\s+работ', r'соискател', r'резюме', r'кандидат', r'поиск\s+работ',
    r'open\s+to\s+work', r'self.?promotion', r'jobseekers?'
]
NEGATIVE = [
    r'vacanc(?:y|ies)\s+only', r'jobs?\s+only', r'no\s+self.?promo', r'no\s+ads',
    r'no\s+advertis', r'employers?\s+only', r'только\s+ваканс', r'без\s+реклам',
    r'реклама\s+запрещ', r'соискател.{0,15}запрещ', r'резюме.{0,15}запрещ'
]

PRIORITY_COUNTRIES = {
    'indonesia','indonesia / bali','indonesia / lombok / ntb','thailand','thailand / phuket',
    'vietnam','malaysia','philippines','philippines / remote','singapore','cambodia',
    'china','japan','japan / international','south korea','korea',
    'mexico','colombia','brazil','brazil / são paulo','argentina','chile','peru','ecuador',
    'uruguay','paraguay','bolivia','costa rica','panama','guatemala','dominican republic / caribbean',
    'latam / remote','spain','spain / barcelona','portugal'
}
EUROPE_COUNTRIES = {
    'netherlands','germany','belgium','france','ireland','uk','united kingdom','italy','italy / milan',
    'malta','cyprus','greece','austria','switzerland','poland','czechia','czech republic','slovakia',
    'slovenia','croatia','hungary','romania','bulgaria','denmark','sweden','norway','finland','estonia',
    'latvia','lithuania','luxembourg'
}


def priority(country):
    c = (country or '').strip().lower()
    if c in PRIORITY_COUNTRIES:
        return 0
    if c in EUROPE_COUNTRIES:
        return 2
    return 1


def cols(conn, table):
    return [r[1] for r in conn.execute(f'pragma table_info("{table}")')]


def pick(candidates, available):
    low = {x.lower(): x for x in available}
    for c in candidates:
        if c in low:
            return low[c]
    return None


def norm_handle(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.startswith('https://t.me/'):
        s = s.split('https://t.me/', 1)[1].split('?', 1)[0].strip('/')
    if s.startswith('http://t.me/'):
        s = s.split('http://t.me/', 1)[1].split('?', 1)[0].strip('/')
    if s.startswith('t.me/'):
        s = s.split('t.me/', 1)[1].split('?', 1)[0].strip('/')
    if s.startswith('@'):
        return s
    if re.fullmatch(r'[A-Za-z0-9_]{5,}', s):
        return '@' + s
    return None


def russianish(text):
    cyr = len(re.findall(r'[А-Яа-яЁё]', text or ''))
    lat = len(re.findall(r'[A-Za-z]', text or ''))
    return cyr > lat and cyr >= 10


def allowed_by_rules(text):
    t = (text or '').lower()
    if any(re.search(p, t, re.S) for p in NEGATIVE):
        return False
    return any(re.search(p, t, re.S) for p in POSITIVE)


def cached_usernames():
    if not SESSION_DB.exists():
        return set()
    con = sqlite3.connect(SESSION_DB, timeout=10)
    try:
        return {
            (r[0] or '').lower()
            for r in con.execute("select username from entities where username is not null and username!=''")
        }
    finally:
        con.close()


async def main():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
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

    available = cols(conn, 'sources')
    handle_col = pick(['username','handle','telegram_username','source_username','chat_username','url','link','source'], available)
    title_col = pick(['title','name','source_name','chat_title'], available)
    country_col = pick(['country','source_country'], available)
    active_col = pick(['active','enabled','is_active','status'], available)
    if not handle_col:
        print('SEEKER_POSTER no usable source handle column; no-op')
        conn.close()
        return

    cached = cached_usernames()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)).isoformat()
    rows = conn.execute('SELECT rowid,* FROM sources ORDER BY rowid DESC').fetchall()

    candidates = []
    for row in rows:
        d = dict(row)
        if active_col:
            av = str(d.get(active_col, '')).strip().lower()
            if av in ('0','false','disabled','inactive','rejected','closed'):
                continue
        handle = norm_handle(d.get(handle_col))
        if not handle:
            continue
        username = handle.lstrip('@').lower()
        # Critical anti-FloodWait rule: poster only touches entities already cached locally.
        if username not in cached:
            continue
        key = handle.lower()
        # Permanently skip groups that have already rejected posting.
        if conn.execute(
            "select 1 from seeker_group_posts where source_key=? and status='blocked' limit 1", (key,)
        ).fetchone():
            continue
        # 14-day per-group cooldown after a successful post.
        if conn.execute(
            "select 1 from seeker_group_posts where source_key=? and status='sent' and posted_at>=? limit 1",
            (key, cutoff)
        ).fetchone():
            continue
        candidates.append((priority(d.get(country_col) if country_col else None), random.random(), handle,
                           str(d.get(title_col) or ''), str(d.get(country_col) or '') if country_col else ''))

    candidates.sort(key=lambda x: (x[0], x[1]))
    print('SEEKER_POSTER candidates_cached_writable=', len(candidates), 'cache=', len(cached))

    posted = 0
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    try:
        for _, _, handle, title_hint, country in candidates:
            if posted >= MAX_POSTS_PER_RUN:
                break
            key = handle.lower()
            try:
                # Because we pre-checked the Telethon session cache, this should not need ResolveUsernameRequest.
                entity = await client.get_entity(handle)
                if isinstance(entity, Channel) and not getattr(entity, 'megagroup', False):
                    continue
                if not isinstance(entity, (Channel, Chat)):
                    continue

                title = getattr(entity, 'title', None) or title_hint or key
                about = ''
                try:
                    if isinstance(entity, Channel):
                        full = await client(GetFullChannelRequest(entity))
                        about = getattr(full.full_chat, 'about', '') or ''
                except Exception:
                    pass

                recent = []
                try:
                    async for m in client.iter_messages(entity, limit=35):
                        if m.message:
                            recent.append(m.message)
                except Exception:
                    pass

                evidence = '\n'.join([title, about] + recent[:35])[:40000]
                if not allowed_by_rules(evidence):
                    continue

                msg = RU_MSG if russianish(evidence) else EN_MSG
                lang = 'ru' if msg == RU_MSG else 'en'
                sent = await client.send_message(entity, msg, link_preview=False)
                ts = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    'insert into seeker_group_posts(source_key,group_title,posted_at,telegram_message_id,language,status,note) values(?,?,?,?,?,?,?)',
                    (key, title, ts, getattr(sent, 'id', None), lang, 'sent', f'rules positive; no CV attached; country={country}')
                conn.commit()
                posted += 1
                print('SEEKER_POST_SENT', key, title, getattr(sent, 'id', None), lang)
                if posted < MAX_POSTS_PER_RUN:
                    await asyncio.sleep(random.uniform(INTER_POST_MIN, INTER_POST_MAX))

            except FloodWaitError as e:
                print('SEEKER_POSTER FLOOD_WAIT', getattr(e, 'seconds', None), 'stopping run')
                break
            except (ChatWriteForbiddenError, UserBannedInChannelError) as e:
                conn.execute(
                    'insert into seeker_group_posts(source_key,group_title,posted_at,telegram_message_id,language,status,note) values(?,?,?,?,?,?,?)',
                    (key, title_hint, datetime.now(timezone.utc).isoformat(), None, None, 'blocked', type(e).__name__)
                )
                conn.commit()
                print('SEEKER_POST_BLOCKED', key, type(e).__name__)
            except Exception as e:
                print('SEEKER_POST_SKIP', key, type(e).__name__, str(e)[:180])
    finally:
        await client.disconnect()
        conn.close()

    print('SEEKER_POSTER_DONE posted=', posted, 'cooldown_days=', COOLDOWN_DAYS, 'max_per_run=', MAX_POSTS_PER_RUN)


if __name__ == '__main__':
    asyncio.run(main())
