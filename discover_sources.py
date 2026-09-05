import os
import re
import json
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat

ROOT = Path('/opt/tg-job-agent')
DB = ROOT / 'telegram_jobs.db'
ENV = ROOT / '.env'
STATE_DB = ROOT / 'discovery_state.db'
SESSION_DB = ROOT / 'telegram_discovery.session'


def load_env():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
client = TelegramClient('/opt/tg-job-agent/telegram_discovery', API_ID, API_HASH)

COUNTRIES = {
    'bali': ('Indonesia', 'Asia/Makassar'), 'indonesia': ('Indonesia', 'Asia/Jakarta'),
    'jakarta': ('Indonesia', 'Asia/Jakarta'), 'surabaya': ('Indonesia', 'Asia/Jakarta'),
    'bandung': ('Indonesia', 'Asia/Jakarta'), 'phuket': ('Thailand', 'Asia/Bangkok'),
    'bangkok': ('Thailand', 'Asia/Bangkok'), 'thailand': ('Thailand', 'Asia/Bangkok'),
    'vietnam': ('Vietnam', 'Asia/Ho_Chi_Minh'), 'ho chi minh': ('Vietnam', 'Asia/Ho_Chi_Minh'),
    'hanoi': ('Vietnam', 'Asia/Ho_Chi_Minh'), 'malaysia': ('Malaysia', 'Asia/Kuala_Lumpur'),
    'kuala lumpur': ('Malaysia', 'Asia/Kuala_Lumpur'), 'philippines': ('Philippines', 'Asia/Manila'),
    'manila': ('Philippines', 'Asia/Manila'), 'singapore': ('Singapore', 'Asia/Singapore'),
    'cambodia': ('Cambodia', 'Asia/Phnom_Penh'), 'phnom penh': ('Cambodia', 'Asia/Phnom_Penh'),
    'china': ('China', 'Asia/Shanghai'), 'shanghai': ('China', 'Asia/Shanghai'),
    'beijing': ('China', 'Asia/Shanghai'), 'shenzhen': ('China', 'Asia/Shanghai'),
    'guangzhou': ('China', 'Asia/Shanghai'), 'japan': ('Japan', 'Asia/Tokyo'),
    'tokyo': ('Japan', 'Asia/Tokyo'), 'osaka': ('Japan', 'Asia/Tokyo'),
    'south korea': ('South Korea', 'Asia/Seoul'), 'korea': ('South Korea', 'Asia/Seoul'),
    'seoul': ('South Korea', 'Asia/Seoul'), 'mexico': ('Mexico', 'America/Mexico_City'),
    'mexico city': ('Mexico', 'America/Mexico_City'), 'colombia': ('Colombia', 'America/Bogota'),
    'bogota': ('Colombia', 'America/Bogota'), 'medellin': ('Colombia', 'America/Bogota'),
    'brazil': ('Brazil', 'America/Sao_Paulo'), 'sao paulo': ('Brazil', 'America/Sao_Paulo'),
    'argentina': ('Argentina', 'America/Argentina/Buenos_Aires'),
    'buenos aires': ('Argentina', 'America/Argentina/Buenos_Aires'),
    'chile': ('Chile', 'America/Santiago'), 'santiago': ('Chile', 'America/Santiago'),
    'peru': ('Peru', 'America/Lima'), 'lima': ('Peru', 'America/Lima'),
    'spain': ('Spain', 'Europe/Madrid'), 'barcelona': ('Spain', 'Europe/Madrid'),
    'madrid': ('Spain', 'Europe/Madrid'), 'portugal': ('Portugal', 'Europe/Lisbon'),
    'lisbon': ('Portugal', 'Europe/Lisbon'), 'porto': ('Portugal', 'Europe/Lisbon'),
}

JOB_WORDS = [
    'job', 'jobs', 'vacancy', 'vacancies', 'hiring', 'career', 'careers', 'recruitment', 'recruiter',
    'work', 'employment', 'lowongan', 'kerja', 'loker', 'jawatan', 'kosong', 'empleo', 'empleos',
    'trabajo', 'trabajos', 'vacante', 'vacantes', 'vaga', 'vagas', 'emprego', 'empregos',
    'việc làm', 'tuyển dụng', 'viec lam', 'tuyen dung', '求人', '採用', '転職', '募集', '招聘',
    '工作', '채용', '구인', '취업', 'งาน', 'รับสมัคร', 'สมัครงาน', 'trabaho', 'работа', 'ваканс',
]
ROLE_WORDS = [
    'manager', 'management', 'project', 'operations', 'operation', 'business development', 'logistics',
    'procurement', 'sales', 'hospitality', 'hotel', 'resort', 'property', 'construction', 'supply chain',
    'warehouse', 'production', 'partnership', 'admin', 'office', 'customer success', 'менеджер',
]

QUERY_BANK = [
    'Indonesia jobs','Indonesia hiring','Indonesia vacancies','Jakarta jobs','Jakarta hiring','Bali jobs',
    'Bali hiring','Surabaya jobs','Bandung jobs','Thailand jobs','Thailand hiring','Bangkok jobs','Phuket jobs',
    'Vietnam jobs','Vietnam hiring','Ho Chi Minh jobs','Hanoi jobs','Malaysia jobs','Malaysia hiring',
    'Kuala Lumpur jobs','Philippines jobs','Philippines hiring','Manila jobs','Singapore jobs','Singapore hiring',
    'Cambodia jobs','Phnom Penh jobs','lowongan kerja','lowongan manager','loker Jakarta','loker Bali',
    'loker Surabaya','jawatan kosong','jawatan kosong Malaysia','kerja kosong Malaysia','việc làm','tuyển dụng',
    'việc làm manager','งาน manager','รับสมัคร manager','สมัครงาน manager','trabaho manager',
    'China jobs','China hiring','Shanghai jobs','Beijing jobs','Shenzhen jobs','Guangzhou jobs','招聘 manager',
    '招聘 运营','招聘 项目经理','Japan jobs','Japan hiring','Tokyo jobs','Osaka jobs','求人 manager','採用 manager',
    '転職 manager','募集 manager','South Korea jobs','Korea hiring','Seoul jobs','채용 manager','구인 manager',
    'Mexico empleos','Mexico vacantes','CDMX empleos','Mexico jobs manager','Colombia empleos','Colombia vacantes',
    'Bogota empleos','Medellin empleos','Argentina empleos','Argentina vacantes','Buenos Aires empleos',
    'Chile empleos','Chile vacantes','Santiago empleos','Peru empleos','Peru vacantes','Lima empleos',
    'empleos project manager','empleos operations manager','empleos gerente','trabajos manager','vacantes manager',
    'vacantes operaciones','Brasil vagas','Brasil empregos','São Paulo vagas','Rio vagas','vagas manager',
    'vagas gerente','vagas operações','empregos manager','España empleos','España vacantes','Madrid empleos',
    'Barcelona empleos','Portugal empregos','Portugal vagas','Lisboa empregos','Porto empregos',
    'remote project manager','remote operations manager','remote business development','remote logistics manager',
    'remote procurement manager','remote sales manager','project manager jobs','operations manager jobs',
    'business development jobs','logistics manager jobs','procurement manager jobs','supply chain jobs',
    'warehouse manager jobs','production manager jobs','hotel manager jobs','resort manager jobs',
    'property manager jobs','office manager jobs','country manager jobs','regional manager jobs','general manager jobs',
]

QUERIES_PER_RUN = 14
SEARCH_LIMIT_PER_QUERY = 80
SEARCH_CUTOFF_DAYS = 45
CACHE_SCAN_PER_RUN = 500
GRAPH_SOURCES_PER_RUN = 40
GRAPH_MESSAGES_PER_SOURCE = 60
TG_LINK_RE = re.compile(r'(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{5,})', re.I)
HANDLE_RE = re.compile(r'(?<![\w])@([A-Za-z][A-Za-z0-9_]{4,})')


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def state_db():
    c = sqlite3.connect(STATE_DB, timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.commit(); return c


def get_state(key, default='0'):
    c = state_db()
    try:
        row = c.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return row[0] if row else default
    finally:
        c.close()


def set_state(key, value):
    c = state_db()
    try:
        c.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        c.commit()
    finally:
        c.close()


def next_queries():
    cursor = int(get_state('query_cursor', '0')) % len(QUERY_BANK)
    out = [QUERY_BANK[(cursor+i) % len(QUERY_BANK)] for i in range(QUERIES_PER_RUN)]
    set_state('query_cursor', (cursor + QUERIES_PER_RUN) % len(QUERY_BANK))
    return out


def infer_geo(text):
    t = (text or '').lower()
    for key, value in COUNTRIES.items():
        if key in t:
            return value
    return None, None


def useful(text):
    t = (text or '').lower()
    return any(w in t for w in JOB_WORDS) or any(w in t for w in ROLE_WORDS)


def source_exists(con, username):
    return con.execute("SELECT id FROM sources WHERE lower(COALESCE(username,''))=lower(?) LIMIT 1", (username,)).fetchone()


def add_source(con, username, title=None, evidence='', origin='unknown'):
    username = (username or '').lstrip('@').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{5,}', username):
        return False
    if source_exists(con, username):
        return False
    geo_text = ' '.join([title or '', evidence or '', username])
    country, tz = infer_geo(geo_text)
    raw = json.dumps({'discovery_origin': origin}, ensure_ascii=False)
    con.execute(
        "INSERT INTO sources(source_id,name,username,telegram_url,source_type,language,country,active,timezone,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f'discover-{username.lower()}', title or username, username, f'https://t.me/{username}', 'channel_or_group', None, country, 1, tz, raw)
    )
    con.commit()
    print('ADDED', username, country or '', 'origin=', origin)
    return True


def cached_channel_candidates():
    if not SESSION_DB.exists():
        return []
    c = sqlite3.connect(SESSION_DB, timeout=15)
    try:
        rows = c.execute("SELECT id,username,name,date FROM entities WHERE username IS NOT NULL AND username!='' ORDER BY date DESC").fetchall()
    finally:
        c.close()
    out = []
    for peer_id, username, name, _ in rows:
        # Telethon stores marked peer IDs. Positive IDs are users; source discovery must never add them.
        if not isinstance(peer_id, int) or peer_id >= 0:
            continue
        text = f'{username or ""} {name or ""}'
        if useful(text):
            out.append((username, name or username))
    return out


def extract_refs(text):
    found = {m.group(1) for m in TG_LINK_RE.finditer(text or '')}
    found.update(m.group(1) for m in HANDLE_RE.finditer(text or ''))
    return found


async def cached_entity_discovery(con):
    candidates = cached_channel_candidates()
    cursor = int(get_state('cache_cursor', '0'))
    batch = [candidates[(cursor+i) % len(candidates)] for i in range(min(CACHE_SCAN_PER_RUN, len(candidates)))] if candidates else []
    if candidates:
        set_state('cache_cursor', (cursor + len(batch)) % len(candidates))
    added = 0
    for username, name in batch:
        if add_source(con, username, name, f'{username} {name}', 'session-cache'):
            added += 1
    return added, len(batch)


async def graph_discovery(con):
    cached_candidates = cached_channel_candidates()
    cached_names = {u.lower() for u, _ in cached_candidates}
    rows = con.execute("SELECT username,name,country FROM sources WHERE active=1 AND username IS NOT NULL AND username!='' ORDER BY id").fetchall()
    # Critical anti-FloodWait invariant: never call get_entity for a source whose username is not already
    # present in this exact discovery session cache. Unknown usernames are left for later cache warming.
    rows = [r for r in rows if (r['username'] or '').lower() in cached_names]
    if not rows:
        return 0, 0, 0
    cursor = int(get_state('graph_cursor', '0')) % len(rows)
    batch = [rows[(cursor+i) % len(rows)] for i in range(min(GRAPH_SOURCES_PER_RUN, len(rows)))]
    set_state('graph_cursor', (cursor + len(batch)) % len(rows))
    added = 0
    refs_seen = 0
    sources_read = 0
    for row in batch:
        username = row['username']
        try:
            entity = await client.get_entity(username)
            if isinstance(entity, Channel) and not (getattr(entity, 'broadcast', False) or getattr(entity, 'megagroup', False)):
                continue
            if not isinstance(entity, (Channel, Chat)):
                continue
            sources_read += 1
            evidence_parts = [getattr(entity, 'title', '') or '']
            async for msg in client.iter_messages(entity, limit=GRAPH_MESSAGES_PER_SOURCE):
                if msg.message:
                    evidence_parts.append(msg.message)
            evidence = '\n'.join(evidence_parts)
            for ref in extract_refs(evidence):
                refs_seen += 1
                # Referenced usernames are promoted only if they are already cache-known job-like chats/channels.
                if ref.lower() not in cached_names:
                    continue
                if add_source(con, ref, ref, evidence[:2000], f'link-graph:{username}'):
                    added += 1
        except FloodWaitError as e:
            print('GRAPH_FLOODWAIT', getattr(e, 'seconds', None), 'stopping graph phase')
            break
        except Exception as e:
            print('GRAPH_SKIP', username, type(e).__name__, str(e)[:120])
        await asyncio.sleep(0.5)
    return added, refs_seen, sources_read


async def global_search_discovery(con, queries, cutoff):
    added = 0
    examined = 0
    unique = set()
    for query in queries:
        try:
            async for msg in client.iter_messages(None, search=query, limit=SEARCH_LIMIT_PER_QUERY):
                examined += 1
                if not msg.date or msg.date < cutoff:
                    continue
                text = msg.message or ''
                if not useful(text):
                    continue
                chat = await msg.get_chat()
                if not chat or not isinstance(chat, (Channel, Chat)):
                    continue
                username = getattr(chat, 'username', None)
                title = getattr(chat, 'title', None) or username
                if not username:
                    continue
                key = username.lower()
                if key in unique:
                    continue
                unique.add(key)
                if add_source(con, username, title, ' '.join([query, title or '', text[:1800]]), f'global-search:{query}'):
                    added += 1
        except FloodWaitError as e:
            print('SEARCH_FLOODWAIT', getattr(e, 'seconds', None), 'stopping global-search phase')
            break
        except Exception as e:
            print('QUERY_FAIL', query, repr(e))
        await asyncio.sleep(3)
    return added, examined, len(unique)


async def main():
    await client.start()
    con = db()
    cols = {r[1] for r in con.execute('PRAGMA table_info(sources)')}
    if 'timezone' not in cols:
        con.execute('ALTER TABLE sources ADD COLUMN timezone TEXT')
        con.commit()

    cutoff = datetime.now(timezone.utc) - timedelta(days=SEARCH_CUTOFF_DAYS)
    queries = next_queries()
    before = con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    print('=== SOURCE DISCOVERY V3 START ===')
    print('sources_before:', before, 'query_bank:', len(QUERY_BANK), 'query_batch:', queries)

    # This phase is fully local and therefore keeps growing the catalogue even during Telegram FloodWait.
    cache_added, cache_examined = await cached_entity_discovery(con)
    print('CACHE_DISCOVERY examined=', cache_examined, 'added=', cache_added)

    graph_added, refs_seen, graph_sources = await graph_discovery(con)
    print('GRAPH_DISCOVERY sources_read=', graph_sources, 'refs_seen=', refs_seen, 'added=', graph_added)

    search_added, examined, unique = await global_search_discovery(con, queries, cutoff)
    print('GLOBAL_SEARCH examined=', examined, 'unique_chats_seen=', unique, 'added=', search_added)

    total = con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    active = con.execute('SELECT COUNT(*) FROM sources WHERE active=1').fetchone()[0]
    print('added_total:', total-before, 'sources_total:', total, 'sources_active:', active)
    print('integrity:', con.execute('PRAGMA integrity_check').fetchone()[0])
    con.close()
    await client.disconnect()
    print('=== SOURCE DISCOVERY V3 DONE ===')


if __name__ == '__main__':
    asyncio.run(main())
