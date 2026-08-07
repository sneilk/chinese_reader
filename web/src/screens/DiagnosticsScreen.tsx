/**
 * Состояние сервиса (T2.10).
 *
 * Сообщения по `error_kind` объясняют отказ конкретной главы, но половина
 * настоящих поломок ими не видна: словарь не импортирован, ключ переводчика
 * не задан, профиль браузера потерян после пересоздания машины. Всё это
 * выглядит одинаково — «не работает», — а чинится по-разному.
 *
 * Поэтому здесь не таблица цифр, а список проверок с ответом «что делать».
 */

import { useEffect, useState } from 'react'
import { ApiError, api, type Diagnostics } from '../api'
import { ErrorNote } from '../components/ErrorNote'

interface Check {
  label: string
  ok: boolean
  detail: string
  advice?: string
}

function buildChecks(d: Diagnostics): Check[] {
  const cedict = d.dict_sources.cedict ?? 0
  const bkrs = d.dict_sources.bkrs ?? 0
  const monthShare = d.month_limit ? Math.round((d.chars_this_month / d.month_limit) * 100) : 0

  return [
    {
      label: 'Схема базы',
      ok: Boolean(d.schema_revision),
      detail: d.schema_revision ?? 'миграции не накатывались',
      advice: 'Выполните alembic upgrade head.',
    },
    {
      label: 'Словарь CC-CEDICT',
      ok: cedict > 0,
      detail: cedict ? `${cedict.toLocaleString('ru-RU')} статей` : 'не импортирован',
      advice: 'Запустите scripts/import_cedict.py — без него карточка будет пустой.',
    },
    {
      label: 'Словарь БКРС',
      ok: bkrs > 0,
      detail: bkrs ? `${bkrs.toLocaleString('ru-RU')} статей` : 'не импортирован',
      advice: 'Необязателен, но без него значения будут английскими: scripts/import_bkrs.py.',
    },
    {
      label: 'Словарь сегментатора',
      ok: d.userdict_words > 0,
      detail: d.userdict_words ? `${d.userdict_words.toLocaleString('ru-RU')} слов` : 'пуст',
      advice: 'Пересоберите userdict — иначе слова будут резаться не так, как ищутся.',
    },
    {
      label: 'Переводчик',
      ok: d.translator_configured,
      detail: d.translator_configured ? 'ключ задан' : 'ключ не задан',
      advice: 'Без ключа главы доходят до segmented и читаются без перевода.',
    },
    {
      label: 'Профиль браузера',
      ok: d.browser_profile_exists,
      detail: d.browser_profile_exists ? 'на месте' : 'отсутствует',
      advice: 'Без него загрузка упрётся в проверку сайта: профиль хранит куки.',
    },
    {
      label: 'Режим браузера',
      ok: !d.browser_headless,
      detail: d.browser_headless ? 'headless' : 'headful под Xvfb',
      advice: 'В headless сайт отдаёт 403 даже с живыми куками (проверено в T0.3).',
    },
    {
      label: 'Расход за месяц',
      ok: monthShare < 90,
      detail: `${d.chars_this_month.toLocaleString('ru-RU')} из ${d.month_limit.toLocaleString('ru-RU')} символов (${monthShare}%)`,
      advice: 'При достижении лимита перевод остановится, чтение — нет.',
    },
  ]
}

export function DiagnosticsScreen() {
  const [data, setData] = useState<Diagnostics | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  const load = () => {
    api
      .diagnostics()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError('network', String(e))))
  }

  useEffect(load, [])

  if (error) {
    return (
      <>
        <h1>Состояние</h1>
        <ErrorNote kind={error.kind} detail={error.message} onRetry={load} />
      </>
    )
  }

  if (!data) return <p className="muted">Загружаю…</p>

  const checks = buildChecks(data)
  const broken = checks.filter((c) => !c.ok)

  return (
    <>
      <h1>Состояние</h1>

      <p className="muted">
        {broken.length === 0
          ? 'Всё на месте.'
          : `Требует внимания: ${broken.length} из ${checks.length}.`}
      </p>

      <ul className="checks">
        {checks.map((check) => (
          <li className={`check${check.ok ? '' : ' check--bad'}`} key={check.label}>
            <span className="check__mark" aria-hidden="true">
              {check.ok ? '✓' : '!'}
            </span>
            <div>
              <div className="check__label">
                {check.label}
                <span className="muted"> — {check.detail}</span>
              </div>
              {!check.ok && check.advice && <div className="check__advice">{check.advice}</div>}
            </div>
          </li>
        ))}
      </ul>

      <p className="muted check__footer">
        Глав: {data.chapters}, предложений: {data.sentences}, своих слов: {data.user_words}. База:{' '}
        {(data.db_size_bytes / 1024 / 1024).toFixed(1)} МБ. Версия {data.version}.
      </p>
    </>
  )
}
