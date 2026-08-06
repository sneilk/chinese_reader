"""Понятия, общие для нескольких слоёв.

Живут отдельно от моделей: загрузчику незачем знать про SQLAlchemy, а слою
БД — про Playwright. Значения совпадают с тем, что лежит в колонках.
"""

import enum


class ChapterStatus(enum.StrEnum):
    """Состояния конвейера загрузки, RFC §4.

    Глава читаема начиная с `segmented`: текст и токены уже есть, переводов
    может не быть.
    """

    FETCHING = "fetching"
    SEGMENTED = "segmented"
    TRANSLATING = "translating"
    READY = "ready"
    FAILED = "failed"


class ErrorKind(enum.StrEnum):
    """Причины отказа. Пользователь должен видеть их различимо, RFC §4.

    Молча отданная пустая глава — худший из отказов, потому что выглядит как
    баг ридера, а не как проблема на той стороне.
    """

    CHALLENGE = "challenge"
    NOT_FOUND = "not_found"
    EMPTY_EXTRACT = "empty_extract"
    FETCH_TIMEOUT = "fetch_timeout"
    ADAPTER_ERROR = "adapter_error"
    TRANSLATE_FAILED = "translate_failed"
