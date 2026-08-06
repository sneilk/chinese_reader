# chinese_reader
Application for learning Chinese while reading books

## Docs
- [RFC: MVP — чтение одной главы с карточками и словарём](docs/rfc-mvp.md)
- [Разбор концепции: предложения и вопросы](docs/concept-review.md)
- [Источники контента: разбор целевых сайтов](docs/sources.md)
- [Сегментация китайского текста: как реализовать](docs/segmentation.md)
- [Провайдер перевода: Яндекс.Переводчик](docs/translation.md)

## Данные

Словарные дампы в репозиторий не кладутся — качаются скриптом при настройке
(`PYTHONPATH=. python scripts/import_cedict.py`).

- **CC-CEDICT** — китайско-английский словарь, [mdbg.net](https://www.mdbg.net/chinese/dictionary?page=cc-cedict),
  лицензия [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Атрибуция обязательна и по условиям лицензии, и по-человечески.
- **БКРС** — большой китайско-русский словарь, [bkrs.info](https://bkrs.info/), ежедневная выгрузка в DSL.
