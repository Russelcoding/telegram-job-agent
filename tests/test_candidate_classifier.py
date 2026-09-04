from candidate_classifier import classify_post, safe_for_auto_send


def test_russian_candidate_self_ad_is_blocked():
    text = '''
    Project Manager, 3+ года опыта.
    Зарплатные ожидания: 170–190 тыс. ₽.
    Формат работы: удаленно или гибрид в Санкт-Петербурге.
    Рассматриваю полную, частичную и проектную занятость.
    Telegram: @vorond0
    '''
    c = classify_post(text)
    assert c.is_candidate_post
    assert not safe_for_auto_send(text)


def test_english_candidate_self_ad_is_blocked():
    text = 'Open to work. Product Manager. 5 years of experience. Expected salary $5000. DM @candidate.'
    c = classify_post(text)
    assert c.is_candidate_post
    assert not c.is_employer_post


def test_clear_russian_vacancy_is_allowed():
    text = '''
    Вакансия: Business Development Manager, Limassol.
    Ищем кандидата в международную компанию.
    Требования: English B2+, опыт продаж.
    Обязанности: развитие партнерской сети.
    Отклик: @recruiter
    '''
    assert safe_for_auto_send(text)


def test_clear_english_vacancy_is_allowed():
    text = '''
    We are hiring a Business Development Manager in Cyprus.
    Requirements: 3+ years in sales.
    Responsibilities: partner acquisition.
    Send your CV to @recruiter.
    '''
    assert safe_for_auto_send(text)


def test_ambiguous_role_post_fails_closed():
    text = 'Senior Project Manager | remote | Telegram @someone | 5 years experience'
    c = classify_post(text)
    assert not c.is_employer_post
    assert not safe_for_auto_send(text)
