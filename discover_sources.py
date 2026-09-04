import os
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient

ROOT = Path('/opt/tg-job-agent')
DB = ROOT / 'telegram_jobs.db'
ENV = ROOT / '.env'
STATE_DB = ROOT / 'discovery_state.db'


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
    'bali': ('Indonesia', 'Asia/Makassar'),
    'indonesia': ('Indonesia', 'Asia/Jakarta'),
    'jakarta': ('Indonesia', 'Asia/Jakarta'),
    'surabaya': ('Indonesia', 'Asia/Jakarta'),
    'bandung': ('Indonesia', 'Asia/Jakarta'),
    'phuket': ('Thailand', 'Asia/Bangkok'),
    'bangkok': ('Thailand', 'Asia/Bangkok'),
    'thailand': ('Thailand', 'Asia/Bangkok'),
    'vietnam': ('Vietnam', 'Asia/Ho_Chi_Minh'),
    'ho chi minh': ('Vietnam', 'Asia/Ho_Chi_Minh'),
    'hanoi': ('Vietnam', 'Asia/Ho_Chi_Minh'),
    'malaysia': ('Malaysia', 'Asia/Kuala_Lumpur'),
    'kuala lumpur': ('Malaysia', 'Asia/Kuala_Lumpur'),
    'philippines': ('Philippines', 'Asia/Manila'),
    'manila': ('Philippines', 'Asia/Manila'),
    'singapore': ('Singapore', 'Asia/Singapore'),
    'cambodia': ('Cambodia', 'Asia/Phnom_Penh'),
    'phnom penh': ('Cambodia', 'Asia/Phnom_Penh'),
    'china': ('China', 'Asia/Shanghai'),
    'shanghai': ('China', 'Asia/Shanghai'),
    'beijing': ('China', 'Asia/Shanghai'),
    'shenzhen': ('China', 'Asia/Shanghai'),
    'guangzhou': ('China', 'Asia/Shanghai'),
    'japan': ('Japan', 'Asia/Tokyo'),
    'tokyo': ('Japan', 'Asia/Tokyo'),
    'osaka': ('Japan', 'Asia/Tokyo'),
    'south korea': ('South Korea', 'Asia/Seoul'),
    'korea': ('South Korea', 'Asia/Seoul'),
    'seoul': ('South Korea', 'Asia/Seoul'),
    'mexico': ('Mexico', 'America/Mexico_City'),
    'mexico city': ('Mexico', 'America/Mexico_City'),
    'colombia': ('Colombia', 'America/Bogota'),
    'bogota': ('Colombia', 'America/Bogota'),
    'medellin': ('Colombia', 'America/Bogota'),
    'brazil': ('Brazil', 'America/Sao_Paulo'),
    'sao paulo': ('Brazil', 'America/Sao_Paulo'),
    'argentina': ('Argentina', 'America/Argentina/Buenos_Aires'),
    'buenos aires': ('Argentina', 'America/Argentina/Buenos_Aires'),
    'chile': ('Chile', 'America/Santiago'),
    'santiago': ('Chile', 'America/Santiago'),
    'peru': ('Peru', 'America/Lima'),
    'lima': ('Peru', 'America/Lima'),
    'spain': ('Spain', 'Europe/Madrid'),
    'barcelona': ('Spain', 'Europe/Madrid'),
    'madrid': ('Spain', 'Europe/Madrid'),
    'portugal': ('Portugal', 'Europe/Lisbon'),
    'lisbon': ('Portugal', 'Europe/Lisbon'),
    'porto': ('Portugal', 'Europe/Lisbon'),
}

# Discovery is intentionally broad. It discovers channels/groups only; vacancy safety
# remains enforced later by scanner -> selector -> worker.
JOB_WORDS = [
    'job', 'jobs', 'vacancy', 'vacancies', 'hiring', 'career', 'careers',
    'recruitment', 'recruiter', 'work', 'employment',
    'lowongan', 'kerja', 'loker', 'jawatan', 'kosong',
    'empleo', 'empleos', 'trabajo', 'trabajos', 'vacante', 'vacantes',
    'vaga', 'vagas', 'emprego', 'empregos',
    'việc làm', 'tuyển dụng', 'viec lam', 'tuyen dung',
    '求人', '採用', '転職', '募集',
    '招聘', '求职', '工作',
    '채용', '구인', '취업',
    'งาน', 'รับสมัคร', 'สมัครงาน',
    'trabaho',
]

ROLE_WORDS = [
    'manager', 'management', 'project', 'operations', 'operation',
    'business development', 'logistics', 'procurement', 'sales', 'hospitality',
    'hotel', 'resort', 'property', 'construction', 'supply chain', 'warehouse',
    'production', 'partnership', 'admin', 'office', 'customer success',
]

# Rotated in small batches to avoid hammering Telegram search while continually
# exploring new geographies, languages, cities and role combinations.
QUERY_BANK = [
    # SEA / English
    'Indonesia jobs', 'Indonesia hiring', 'Indonesia vacancies', 'Jakarta jobs',
    'Jakarta hiring', 'Bali jobs', 'Bali hiring', 'Surabaya jobs', 'Bandung jobs',
    'Thailand jobs', 'Thailand hiring', 'Bangkok jobs', 'Phuket jobs',
    'Vietnam jobs', 'Vietnam hiring', 'Ho Chi Minh jobs', 'Hanoi jobs',
    'Malaysia jobs', 'Malaysia hiring', 'Kuala Lumpur jobs',
    'Philippines jobs', 'Philippines hiring', 'Manila jobs',
    'Singapore jobs', 'Singapore hiring', 'Cambodia jobs', 'Phnom Penh jobs',
    # SEA / local language
    'lowongan kerja', 'lowongan manager', 'loker Jakarta', 'loker Bali',
    'loker Surabaya', 'jawatan kosong', 'jawatan kosong Malaysia',
    'kerja kosong Malaysia', 'việc làm', 'tuyển dụng', 'việc làm manager',
    'งาน manager', 'รับสมัคร manager', 'สมัครงาน manager', 'trabaho manager',
    # China / Japan / Korea
    'China jobs', 'China hiring', 'Shanghai jobs', 'Beijing jobs', 'Shenzhen jobs',
    'Guangzhou jobs', '招聘 manager', '招聘 运营', '招聘 项目经理',
    'Japan jobs', 'Japan hiring', 'Tokyo jobs', 'Osaka jobs',
    '求人 manager', '採用 manager', '転職 manager', '募集 manager',
    'South Korea jobs', 'Korea hiring', 'Seoul jobs', '채용 manager', '구인 manager',
    # LATAM / Spanish
    'Mexico empleos', 'Mexico vacantes', 'CDMX empleos', 'Mexico jobs manager',
    'Colombia empleos', 'Colombia vacantes', 'Bogota empleos', 'Medellin empleos',
    'Argentina empleos', 'Argentina vacantes', 'Buenos Aires empleos',
    'Chile empleos', 'Chile vacantes', 'Santiago empleos',
    'Peru empleos', 'Peru vacantes', 'Lima empleos',
    'empleos project manager', 'empleos operations manager', 'empleos gerente',
    'trabajos manager', 'vacantes manager', 'vacantes operaciones',
    # Brazil / Portuguese
    'Brasil vagas', 'Brasil empregos', 'São Paulo vagas', 'Rio vagas',
    'vagas manager', 'vagas gerente', 'vagas operações', 'empregos manager',
    # Spain / Portugal
    'España empleos', 'España vacantes', 'Madrid empleos', 'Barcelona empleos',
    'Portugal empregos', 'Portugal vagas', 'Lisboa empregos', 'Porto empregos',
    # Global / remote / role-based
    'remote project manager', 'remote operations manager', 'remote business development',
    'remote logistics manager', 'remote procurement manager', 'remote sales manager',
    'project manager jobs', 'operations manager jobs', 'business development jobs',
    'logistics manager jobs', 'procurement manager jobs', 'supply chain jobs',
    'warehouse manager jobs', 'production manager jobs', 'hotel manager jobs',
    'resort manager jobs', 'property manager jobs', 'office manager jobs',
    'country manager jobs', 'regional manager jobs', 'general manager jobs',
]

QUERIES_PER_RUN = 14
SEARCH_LIMIT_PER_QUERY = 80
SEARCH_CUTOFF_DAYS = 45


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def state_db():
    c = sqlite3.connect(STATE_DB, timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.commit()
    return c


def load_cursor():
    c = state_db()
    try:
        row = c.execute("SELECT value FROM state WHERE key='query_cursor'").fetchone()
        return int(row[0]) if row else 0
    finally:
        c.close()


def save_cursor(value):
    c = state_db()
    try:
        c.execute(
            "INSERT INTO state(key,value) VALUES('query_cursor',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(value),),
        )
        c.commit()
    finally:
        c.close()


def next_queries():
    if not QUERY_BANK:
        return []
    cursor = load_cursor() % len(QUERY_BANK)
    out = [QUERY_BANK[(cursor + i) % len(QUERY_BANK)] for i in range(QUERIES_PER_RUN)]
    save_cursor((cursor + QUERIES_PER_RUN) % len(QUERY_BANK))
    return out


def infer_geo(text):
    t = (text or '').lower()
    for key, (country, tz) in COUNTRIES.items():
        if key in t:
            return country, tz
    return None, None


def useful(text):
    t = (text or '').lower()
    return any(w in t for w in JOB_WORDS) or any(w in t for w in ROLE_WORDS)


async def main():
    await client.start()
    con = db()
    cols = {r[1] for r in con.execute('PRAGMA table_info(sources)')}
    if 'timezone' not in cols:
        con.execute('ALTER TABLE sources ADD COLUMN timezone TEXT')
        con.commit()

    cutoff = datetime.now(timezone.utc) - timedelta(days=SEARCH_CUTOFF_DAYS)
    queries = next_queries()
    added = 0
    updated = 0
    examined = 0
    discovered_usernames = set()

    print('=== SOURCE DISCOVERY START ===')
    print('query_bank:', len(QUERY_BANK))
    print('queries_this_run:', len(queries))
    print('query_batch:', queries)

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
                if not chat:
                    continue
                username = getattr(chat, 'username', None)
                title = getattr(chat, 'title', None) or username
                if not username:
                    continue
                username = username.strip()
                if not username or username.lower() in discovered_usernames:
                    continue
                discovered_usernames.add(username.lower())

                geo_text = ' '.join([query, title or '', text[:1800]])
                country, tz = infer_geo(geo_text)
                existing = con.execute(
                    "SELECT id,country,timezone,active FROM sources "
                    "WHERE lower(COALESCE(username,''))=lower(?) LIMIT 1",
                    (username,),
                ).fetchone()

                if existing:
                    changed = False
                    new_country = existing['country']
                    new_tz = existing['timezone']
                    if country and not (new_country or '').strip():
                        new_country = country
                        changed = True
                    if tz and not (new_tz or '').strip():
                        new_tz = tz
                        changed = True
                    if not existing['active']:
                        changed = True
                    if changed:
                        con.execute(
                            "UPDATE sources SET country=?, timezone=?, active=1 WHERE id=?",
                            (new_country, new_tz, existing['id']),
                        )
                        con.commit()
                        updated += 1
                    continue

                con.execute(
                    "INSERT INTO sources("
                    "source_id,name,username,telegram_url,source_type,language,country,active,timezone,raw_json"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        f'discover-{username.lower()}', title, username,
                        f'https://t.me/{username}', 'channel_or_group', None,
                        country, 1, tz, '{}'
                    ),
                )
                con.commit()
                added += 1
                print('ADDED', username, country or '')
        except Exception as e:
            print('QUERY_FAIL', query, repr(e))

        # Gentle pacing; discovery should grow continuously without causing another flood.
        await asyncio.sleep(3)

    total = con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    active = con.execute('SELECT COUNT(*) FROM sources WHERE active=1').fetchone()[0]
    print('examined:', examined)
    print('unique_chats_seen:', len(discovered_usernames))
    print('added:', added)
    print('updated:', updated)
    print('sources_total:', total)
    print('sources_active:', active)
    print('integrity:', con.execute('PRAGMA integrity_check').fetchone()[0])
    con.close()
    print('=== SOURCE DISCOVERY DONE ===')


if __name__ == '__main__':
    asyncio.run(main())
