from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone

ROOT = Path('/opt/tg-job-agent')
WORKER = ROOT / 'worker.py'
SCANNER = ROOT / 'scanner.py'
SELECTOR = ROOT / 'selector.py'
DB = ROOT / 'telegram_jobs.db'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def backup(path: Path) -> None:
    shutil.copy2(path, path.with_name(path.name + f'.candidate-guard-{STAMP}.bak'))


def patch_scanner() -> None:
    p = SCANNER
    s = p.read_text()
    if 'from candidate_classifier import classify_post' not in s:
        s = s.replace('from telethon import TelegramClient\n', 'from telethon import TelegramClient\nfrom candidate_classifier import classify_post\n', 1)
    old = """def looks_like_job(text):\n    t = text.lower()\n    if any((x in t for x in BAD_WORDS)):\n        return False\n    return any((x in t for x in ROLE_WORDS))\n"""
    new = """def looks_like_job(text):\n    # Level 1 safety: scanner accepts only positively identified employer vacancies.\n    classification = classify_post(text)\n    if not classification.is_employer_post:\n        return False\n    t = text.lower()\n    if any((x in t for x in BAD_WORDS)):\n        return False\n    return any((x in t for x in ROLE_WORDS))\n"""
    if old in s:
        s = s.replace(old, new, 1)
    elif 'classification = classify_post(text)' not in s:
        raise RuntimeError('scanner looks_like_job shape changed')
    p.write_text(s)


def patch_selector() -> None:
    p = SELECTOR
    s = p.read_text()
    if 'from candidate_classifier import classify_post' not in s:
        s = s.replace('from zoneinfo import ZoneInfo\n', 'from zoneinfo import ZoneInfo\nfrom candidate_classifier import classify_post\n', 1)
    marker = """    text = (row[\"raw_text\"] or \"\") + \"\\n\" + (row[\"title\"] or \"\")\n\n    if already_blocked(con,row[\"contact\"]):\n"""
    replacement = """    text = (row[\"raw_text\"] or \"\") + \"\\n\" + (row[\"title\"] or \"\")\n\n    # Level 2 safety: selector requires positive employer/recruiter hiring intent.\n    classification = classify_post(text)\n    if not classification.is_employer_post:\n        con.execute(\n            \"UPDATE jobs SET selector_status='rejected_safety' WHERE id=?\",\n            (row[\"id\"],)\n        )\n        rejected += 1\n        continue\n\n    if already_blocked(con,row[\"contact\"]):\n"""
    if marker in s:
        s = s.replace(marker, replacement, 1)
    elif "selector_status='rejected_safety'" not in s:
        raise RuntimeError('selector loop shape changed')
    p.write_text(s)


def patch_worker() -> None:
    p = WORKER
    s = p.read_text()
    if 'from candidate_classifier import classify_post' not in s:
        s = s.replace('from telethon.errors import RPCError\n', 'from telethon.errors import RPCError\nfrom candidate_classifier import classify_post\n', 1)
    helper = '''\n\ndef job_source_text(con, job_id):\n    if not job_id:\n        return ''\n    row = con.execute(\n        "SELECT raw_text, title FROM jobs WHERE job_id=? LIMIT 1",\n        (job_id,)\n    ).fetchone()\n    if not row:\n        return ''\n    return ((row['raw_text'] or '') + "\\n" + (row['title'] or '')).strip()\n\ndef outbound_employer_verified(con, row):\n    text = job_source_text(con, row['job_id'])\n    classification = classify_post(text)\n    return classification.is_employer_post, classification.reason\n'''
    anchor = '\ndef mark_skipped(con, row_id, reason):\n'
    if 'def outbound_employer_verified' not in s:
        if anchor not in s:
            raise RuntimeError('worker helper anchor changed')
        s = s.replace(anchor, helper + anchor, 1)
    marker = """        if not Path(cv_path).exists():\n            mark_failed(con, row['id'], f'CV missing: {cv_path}')\n            print('CV MISSING', cv_path)\n            return\n        try:\n            entity = await client.get_entity(recipient)\n"""
    replacement = """        if not Path(cv_path).exists():\n            mark_failed(con, row['id'], f'CV missing: {cv_path}')\n            print('CV MISSING', cv_path)\n            return\n        # Level 3 mandatory outbound safety gate. Fail closed immediately before Telegram.\n        verified, safety_reason = outbound_employer_verified(con, row)\n        if not verified:\n            mark_skipped(con, row['id'], 'outbound_safety:' + safety_reason)\n            print('SKIP_UNVERIFIED_EMPLOYER', recipient, safety_reason)\n            return\n        try:\n            entity = await client.get_entity(recipient)\n"""
    if marker in s:
        s = s.replace(marker, replacement, 1)
    elif 'SKIP_UNVERIFIED_EMPLOYER' not in s:
        raise RuntimeError('worker send path shape changed')
    p.write_text(s)


def quarantine_existing_pending() -> int:
    from candidate_classifier import classify_post
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,job_id FROM send_queue WHERE status='pending'").fetchall()
    blocked = 0
    for row in rows:
        j = con.execute("SELECT raw_text,title FROM jobs WHERE job_id=? LIMIT 1", (row['job_id'],)).fetchone()
        text = '' if not j else ((j['raw_text'] or '') + '\n' + (j['title'] or ''))
        c = classify_post(text)
        if not c.is_employer_post:
            con.execute(
                "UPDATE send_queue SET status='skipped', error=?, processed_at=? WHERE id=?",
                ('outbound_safety:' + c.reason, datetime.now(timezone.utc).isoformat(), row['id'])
            )
            blocked += 1
    con.commit()
    con.close()
    return blocked


def main() -> None:
    for p in (WORKER, SCANNER, SELECTOR, DB):
        if not p.exists():
            raise SystemExit(f'missing production path: {p}')
    for p in (WORKER, SCANNER, SELECTOR):
        backup(p)
    patch_scanner()
    patch_selector()
    patch_worker()
    blocked = quarantine_existing_pending()
    print('candidate_guard_applied=1')
    print('pending_quarantined=', blocked)


if __name__ == '__main__':
    main()
