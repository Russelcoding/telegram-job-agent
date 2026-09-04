import time
import os
import re
import sqlite3
import hashlib
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from candidate_classifier import classify_post

# --- persistent resolve guard injected by repair ---
_RESOLVE_GUARD_DB = "/opt/tg-job-agent/resolve_guard.db"
def _rg_conn():
    import sqlite3
    c=sqlite3.connect(_RESOLVE_GUARD_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS source_guard (key TEXT PRIMARY KEY, next_try REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, note TEXT)")
    c.commit(); return c
def _rg_allowed(key):
    try:
        c=_rg_conn(); r=c.execute("SELECT next_try FROM source_guard WHERE key=?",(str(key),)).fetchone(); c.close()
        return not r or float(r[0] or 0) <= time.time()
    except Exception:
        return True
def _rg_fail(key, seconds, note=''):
    try:
        c=_rg_conn(); c.execute("INSERT INTO source_guard(key,next_try,failures,note) VALUES(?,?,1,?) ON CONFLICT(key) DO UPDATE SET next_try=excluded.next_try, failures=source_guard.failures+1, note=excluded.note",(str(key),time.time()+int(seconds),str(note)[:300])); c.commit(); c.close()
    except Exception: pass
def _rg_ok(key):
    try:
        c=_rg_conn(); c.execute("DELETE FROM source_guard WHERE key=?",(str(key),)); c.commit(); c.close()
    except Exception: pass
# --- end resolve guard ---
ROOT = Path('/opt/tg-job-agent')
DB = ROOT / 'telegram_jobs.db'
ENV = ROOT / '.env'

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
client = TelegramClient('/opt/tg-job-agent/telegram_scanner', API_ID, API_HASH)
ROLE_WORDS = ['general manager', 'operations manager', 'operation manager', 'project manager', 'program manager', 'property manager', 'construction manager', 'development manager', 'business development', 'sales manager', 'account manager', 'customer success', 'procurement manager', 'purchasing manager', 'supply chain', 'logistics manager', 'warehouse manager', 'production manager', 'e-commerce manager', 'ecommerce manager', 'marketplace manager', 'partnerships manager', 'expansion manager', 'branch manager', 'country manager', 'regional manager', 'area manager', 'hotel manager', 'resort manager', 'restaurant manager', 'f&b manager', 'office manager', 'service manager', 'community manager']
BAD_WORDS = ['internship', 'intern ', 'unpaid', 'volunteer', 'стажировка', 'без оплаты']
CONTACT_RE = re.compile('(?<![\\w])@([A-Za-z0-9_]{5,32})')

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c

def now():
    return datetime.now(timezone.utc).isoformat()

def normalize_username(v):
    if not v:
        return None
    v = v.strip()
    v = v.replace('https://t.me/', '').replace('http://t.me/', '')
    v = v.lstrip('@').split('?')[0].split('/')[0]
    return v or None

def looks_like_job(text):
    # Level 1 safety: scanner accepts only positively identified employer vacancies.
    classification = classify_post(text)
    if not classification.is_employer_post:
        return False
    t = text.lower()
    if any((x in t for x in BAD_WORDS)):
        return False
    return any((x in t for x in ROLE_WORDS))

def title_from_text(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for line in lines[:8]:
        low = line.lower()
        if any((x in low for x in ROLE_WORDS)):
            return line[:180]
    return lines[0][:180] if lines else 'Telegram job'

def dedup(source, msg_id, text):
    raw = f'{source}:{msg_id}:{text[:500]}'
    return hashlib.sha256(raw.encode()).hexdigest()

async def scan_source(source):
    username = normalize_username(source['username']) or normalize_username(source['telegram_url'])
    if not username:
        return 0
    try:
        entity = await client.get_entity(username)
    except Exception as e:
        print('SOURCE_FAIL', username, repr(e))
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    found = 0
    try:
        async for msg in client.iter_messages(entity, limit=150):
            if not msg.message:
                continue
            dt = msg.date
            if dt and dt < cutoff:
                break
            text = msg.message.strip()
            if not looks_like_job(text):
                continue
            contacts = ['@' + x for x in CONTACT_RE.findall(text)]
            contact = contacts[0] if contacts else None
            key = dedup(username, msg.id, text)
            con = db()
            try:
                exists = con.execute('SELECT 1 FROM jobs WHERE dedup_key=? LIMIT 1', (key,)).fetchone()
                if exists:
                    continue
                link = f'https://t.me/{username}/{msg.id}'
                con.execute('\n                INSERT INTO jobs(\n                    job_id,title,source,telegram_url,contact,country,\n                    posted_at,raw_text,status,dedup_key,found_at,raw_json\n                )\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)\n                ', (f'{username}-{msg.id}', title_from_text(text), username, link, contact, source['country'], dt.isoformat() if dt else None, text, 'new', key, now(), '{}'))
                con.commit()
                found += 1
            finally:
                con.close()
    except Exception as e:
        print('SCAN_FAIL', username, repr(e))
    con = db()
    try:
        con.execute('UPDATE sources SET last_scanned_at=? WHERE id=?', (now(), source['id']))
        con.commit()
    finally:
        con.close()
    return found

async def main():
    await client.start()
    con = db()
    sources = con.execute('\n        SELECT *\n        FROM sources\n        WHERE active=1\n          AND (\n            username IS NOT NULL\n            OR telegram_url IS NOT NULL\n          )\n        ORDER BY\n          CASE WHEN last_scanned_at IS NULL THEN 0 ELSE 1 END,\n          last_scanned_at ASC\n        LIMIT 120\n    ').fetchall()
    con.close()
    total = 0
    print('=== TELEGRAM SCANNER START ===')
    print('sources:', len(sources))
    for source in sources:
        _guard_key = (source.get('username') if hasattr(source, 'get') else None) or (source.get('source_key') if hasattr(source, 'get') else None) or (source.get('id') if hasattr(source, 'get') else None) or str(source)
        if not _rg_allowed(_guard_key):
            continue
        n = await scan_source(source)
        total += n
    print('new_jobs:', total)
    print('=== TELEGRAM SCANNER DONE ===')
if __name__ == '__main__':
    asyncio.run(main())
