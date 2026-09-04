from candidate_classifier import classify_post, safe_for_auto_send


BLOCK_CASES = [
    "Project Manager. 3 years experience. Looking for work. Telegram @candidate",
    "#resume Senior Developer, 5 years experience, salary expectations...",
    "Ищу работу менеджером проекта. Опыт 4 года.",
    "Open to work — Python developer.",
    "Looking for new opportunities. CV available on request.",
    "Project Manager, 3+ года опыта. Зарплатные ожидания: 170–190 тыс. ₽. Формат работы: удаленно или гибрид. Telegram @vorond0",
    "Senior Project Manager | remote | Telegram @someone | 5 years experience",
    "Project Manager / vacancy keywords in profile. Open to work. My experience: 4 years. Telegram @candidate",
]

ALLOW_CASES = [
    "We are hiring a Project Manager. Send your CV to jobs@example.com",
    "Требуется Project Manager. Опыт 3+ года. Резюме отправлять на hr@example.com",
    "Vacancy: Operations Manager. Responsibilities: lead operations. Requirements: 3+ years. Apply via jobs@example.com",
    "Вакансия: Business Development Manager. Ищем сотрудника. Требования: English B2+. Обязанности: развитие партнерской сети. Отклик: @recruiter",
]


def test_block_cases_fail_closed():
    for text in BLOCK_CASES:
        c = classify_post(text)
        assert not c.is_employer_post, (text, c)
        assert not safe_for_auto_send(text), (text, c)


def test_allow_cases_have_positive_employer_intent():
    for text in ALLOW_CASES:
        c = classify_post(text)
        assert c.is_employer_post, (text, c)
        assert safe_for_auto_send(text), (text, c)


def test_candidate_signal_overrides_mixed_employer_wording():
    text = "Vacancy / Project Manager. Looking for work. Salary expectations 5000 EUR. My CV: @candidate"
    c = classify_post(text)
    assert c.is_candidate_post
    assert not c.is_employer_post


def test_ambiguous_mixed_language_case_is_blocked():
    text = "Project Manager | удаленно | 4 years experience | Telegram @person"
    c = classify_post(text)
    assert not c.is_employer_post
    assert not safe_for_auto_send(text)
