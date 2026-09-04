import re
from dataclasses import dataclass


CANDIDATE_MARKERS = [
    '#резюме', '#resume', '#cv', '#opentowork', '#open_to_work', 'open to work',
    'ищу работу', 'ищу вакансию', 'в поиске работы', 'нахожусь в поиске',
    'рассматриваю вакансии', 'рассматриваю предложения', 'рассматриваю позиции',
    'ищу новую работу', 'готов рассмотреть предложения', 'готова рассмотреть предложения',
    'looking for a job', 'looking for work', 'seeking a job', 'seeking opportunities',
    'seeking new opportunities', 'looking for new opportunities', 'available for work',
    'my resume', 'my cv', 'резюме:', 'мое резюме', 'моё резюме',
    'зарплатные ожидания', 'salary expectations', 'expected salary',
    'желаемая зарплата', 'желаемая должность', 'desired position', 'desired salary',
    'мои компетенции', 'мои навыки', 'мой опыт', 'обо мне', 'about me',
]

EMPLOYER_MARKERS = [
    'вакансия', 'ищем ', 'ищем:', 'ищет ', 'требуется', 'требуются', 'нанимаем',
    'открыта позиция', 'открыта вакансия', 'в команду нужен', 'в команду нужна',
    'we are hiring', "we're hiring", 'hiring ', 'vacancy', 'job opening',
    'position open', 'join our team', 'apply for', 'send your cv', 'send cv',
    'отправляйте резюме', 'присылайте резюме', 'откликнуться', 'отклик',
]

CANDIDATE_PATTERNS = [
    r'\b(?:мой|мои|моя)\s+(?:опыт|навыки|компетенции|стек)\b',
    r'\bопыт работы\b.{0,120}\b(?:лет|года|год)\b',
    r'\bi have\s+\d+\+?\s+years?\s+of\s+experience\b',
    r'\bexperience\s*:\s*\d+\+?\s*years?\b',
    r'\bготов(?:а)?\s+к\s+(?:работе|релокации|переезду)\b',
    r'\bпредпочитаемый\s+формат\b',
    r'\bформат\s+(?:работы\s*)?:\s*(?:удален|удалён|гибрид|офис)',
    r'\bлокация\s*:\s*[^\n]+',
    r'\bгород\s*:\s*[^\n]+',
]

EMPLOYER_PATTERNS = [
    r'\bобязанност(?:и|ями)\b',
    r'\bтребовани(?:я|ями)\b',
    r'\brequirements?\b',
    r'\bresponsibilit(?:y|ies)\b',
    r'\bwhat you(?:\'|’)ll do\b',
    r'\bwhat we offer\b',
    r'\bусловия\s*:\s*',
]


@dataclass(frozen=True)
class Classification:
    is_employer_post: bool
    is_candidate_post: bool
    reason: str
    employer_score: int
    candidate_score: int


def _norm(text: str) -> str:
    return (text or '').lower().replace('ё', 'е')


def classify_post(text: str) -> Classification:
    """Fail closed: auto-send only when employer intent is positively established."""
    t = _norm(text)
    if not t.strip():
        return Classification(False, False, 'empty', 0, 0)

    candidate_hits = [m for m in CANDIDATE_MARKERS if m in t]
    employer_hits = [m for m in EMPLOYER_MARKERS if m in t]
    candidate_pattern_hits = sum(bool(re.search(p, t, re.S)) for p in CANDIDATE_PATTERNS)
    employer_pattern_hits = sum(bool(re.search(p, t, re.S)) for p in EMPLOYER_PATTERNS)

    candidate_score = len(candidate_hits) * 3 + candidate_pattern_hits
    employer_score = len(employer_hits) * 3 + employer_pattern_hits

    # Strong candidate/self-ad signal always wins over generic vacancy-ish language.
    if candidate_hits:
        return Classification(False, True, 'candidate_marker:' + candidate_hits[0], employer_score, candidate_score)

    if candidate_pattern_hits >= 2 and employer_score < 6:
        return Classification(False, True, 'candidate_profile', employer_score, candidate_score)

    # Positive employer intent is mandatory for automatic outbound contact.
    if employer_score >= 3 and employer_score > candidate_score:
        return Classification(True, False, 'employer_intent', employer_score, candidate_score)

    return Classification(False, False, 'ambiguous_no_employer_intent', employer_score, candidate_score)


def safe_for_auto_send(text: str) -> bool:
    return classify_post(text).is_employer_post
