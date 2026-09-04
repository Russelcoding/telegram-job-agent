import os
import sqlite3
import asyncio
import random
from pathlib import Path
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.errors import RPCError
from candidate_classifier import classify_post
ROOT = Path('/opt/tg-job-agent')
DB = ROOT / 'telegram_jobs.db'
ENV = ROOT / '.env'
DEFAULT_CV = ROOT / 'cv' / 'Ruslans_Strakis_CV.pdf'
MIN_SEND_DELAY_SECONDS = 120
MAX_SEND_DELAY_SECONDS = 300
EMPTY_QUEUE_POLL_SECONDS = 20

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
client = TelegramClient('/opt/tg-job-agent/telegram_worker', API_ID, API_HASH)

def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con

def now():
    return datetime.now(timezone.utc).isoformat()

def normalize_contact(v):
    if not v:
        return ''
    v = v.strip()
    if not v.startswith('@'):
        v = '@' + v
    return v.lower()

def already_contacted(con, recipient):
    r = normalize_contact(recipient)
    row = con.execute("\n        SELECT 1\n        FROM contacts\n        WHERE lower(contact)=?\n          AND status='contacted'\n        LIMIT 1\n        ", (r,)).fetchone()
    if row:
        return True
    row = con.execute("\n        SELECT 1\n        FROM applications\n        WHERE lower(recipient)=?\n          AND (\n            sent_at IS NOT NULL\n            OR lower(status) IN ('sent','done','success','successful')\n          )\n        LIMIT 1\n        ", (r,)).fetchone()
    return bool(row)


def job_source_text(con, job_id):
    if not job_id:
        return ''
    row = con.execute(
        "SELECT raw_text, title FROM jobs WHERE job_id=? LIMIT 1",
        (job_id,)
    ).fetchone()
    if not row:
        return ''
    return ((row['raw_text'] or '') + "\n" + (row['title'] or '')).strip()

def outbound_employer_verified(con, row):
    text = job_source_text(con, row['job_id'])
    classification = classify_post(text)
    return classification.is_employer_post, classification.reason

def mark_skipped(con, row_id, reason):
    con.execute("\n        UPDATE send_queue\n        SET status='skipped',\n            error=?,\n            processed_at=?\n        WHERE id=?\n        ", (reason, now(), row_id))
    con.commit()

def mark_failed(con, row_id, error):
    con.execute("\n        UPDATE send_queue\n        SET status='failed',\n            error=?,\n            processed_at=?\n        WHERE id=?\n        ", (str(error)[:2000], now(), row_id))
    con.commit()

def mark_sent(con, row_id, recipient, message, cv_path, msg_id, job_id):
    ts = now()
    con.execute("\n        UPDATE send_queue\n        SET status='sent',\n            telegram_message_id=?,\n            processed_at=?,\n            error=NULL\n        WHERE id=?\n        ", (str(msg_id), ts, row_id))
    con.execute("\n        INSERT INTO contacts(\n            contact,\n            first_contact_at,\n            last_message_id,\n            status\n        )\n        VALUES (?,?,?,'contacted')\n        ON CONFLICT(contact) DO UPDATE SET\n            first_contact_at=COALESCE(\n                contacts.first_contact_at,\n                excluded.first_contact_at\n            ),\n            last_message_id=excluded.last_message_id,\n            status='contacted'\n        ", (normalize_contact(recipient), ts, str(msg_id)))
    con.execute('\n        INSERT INTO applications(\n            job_id,\n            recipient,\n            message,\n            cv_name,\n            status,\n            sent_at,\n            telegram_message_id,\n            raw_json\n        )\n        VALUES (?,?,?,?,?,?,?,?)\n        ', (job_id, recipient, message, Path(cv_path).name, 'sent', ts, str(msg_id), '{}'))
    con.commit()

async def process_one(row):
    con = db()
    try:
        recipient = row['recipient']
        message = row['message']
        cv_path = row['cv_path'] or str(DEFAULT_CV)
        if already_contacted(con, recipient):
            print('SKIP CONTACTED', recipient)
            mark_skipped(con, row['id'], 'contact_already_contacted')
            return False
        if not Path(cv_path).exists():
            mark_failed(con, row['id'], f'CV missing: {cv_path}')
            print('CV MISSING', cv_path)
            return False
        # Level 3 mandatory outbound safety gate. Fail closed immediately before Telegram.
        verified, safety_reason = outbound_employer_verified(con, row)
        if not verified:
            mark_skipped(con, row['id'], 'outbound_safety:' + safety_reason)
            print('SKIP_UNVERIFIED_EMPLOYER', recipient, safety_reason)
            return False
        try:
            entity = await client.get_entity(recipient)
        except Exception as e:
            mark_failed(con, row['id'], f'Invalid/unresolvable recipient: {e}')
            print('INVALID_RECIPIENT', recipient, repr(e))
            return False
        try:
            msg = await client.send_message(entity, message, file=cv_path)
        except RPCError as e:
            mark_failed(con, row['id'], e)
            print('SEND FAILED', recipient, repr(e))
            return False
        mark_sent(con, row['id'], recipient, message, cv_path, msg.id, row['job_id'])
        print('SENT', recipient, 'MESSAGE_ID', msg.id)
        return True
    finally:
        con.close()

async def main():
    if not DB.exists():
        raise SystemExit(f'DB missing: {DB}')
    if not DEFAULT_CV.exists():
        raise SystemExit(f'CV missing: {DEFAULT_CV}')
    await client.start()
    print('=== SQLITE TELEGRAM WORKER READY ===')
    print('DB:', DB)
    print('CV:', DEFAULT_CV.name)
    print(f'OUTBOUND PACING: one send every {MIN_SEND_DELAY_SECONDS}-{MAX_SEND_DELAY_SECONDS}s')
    while True:
        con = db()
        row = con.execute("\n            SELECT *\n            FROM send_queue\n            WHERE status='pending'\n            ORDER BY id ASC\n            LIMIT 1\n            ").fetchone()
        con.close()
        if not row:
            await asyncio.sleep(EMPTY_QUEUE_POLL_SECONDS)
            continue
        sent = await process_one(row)
        if sent:
            delay = random.randint(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            print('NEXT_SEND_DELAY_SECONDS', delay)
            await asyncio.sleep(delay)
        else:
            # Invalid/skipped items should not create multi-minute stalls.
            await asyncio.sleep(5)
if __name__ == '__main__':
    asyncio.run(main())