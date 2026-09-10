/**
 * Состояние сервиса (T2.10).
 *
 * Сообщения по `error_kind` объясняют отказ конкретной главы, но половина
 * настоящих поломок ими не видна: словарь не импортирован, ключ переводчика
 * не задан, профиль браузера потерян после пересоздания машины. Всё это
 * выглядит одинаково — «не работает», — а чинится по-разному.
 *
 * Поэтому здесь не таблица цифр, а список проверок с ответом «что делать».
 *
 * Одна проверка выбивается из ряда, и намеренно. Всё остальное здесь читается
 * из настроек и базы, а озвучка так не проверяется: «ключ задан» верно и тогда,
 * когда синтез ответит 403, — ключ может быть от того же сервисного аккаунта,
 * что и у переводчика, без роли `ai.speechkit-tts.user`. Узнать это можно,
 * только попробовав, поэтому рядом с ней стоит кнопка, а не галочка.
 */

import { useEffect, useState } from 'react'
import {
  ApiError,
  asApiError,
  api,
  browserScreenshotUrl,
  type BrowserCheck,
  type Diagnostics,
  type SpeechCheck,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { describeBrowserCheck } from '../errors'
import {
  THEME_ORDER,
  applyTheme,
  describeTheme,
  loadTheme,
  saveTheme,
  type ThemeChoice,
} from '../theme'

interface Check {
  label: string
  ok: boolean
  detail: string
  advice?: string
  /** Ключ проверки, которую можно выполнить вживую. */
  probe?: 'speech' | 'browser'
}

function share(used: number, limit: number): number {
  return limit ? Math.round((used / limit) * 100) : 0
}

function buildChecks(d: Diagnostics): Check[] {
  const cedict = d.dict_sources.cedict ?? 0
  const bkrs = d.dict_sources.bkrs ?? 0
  const endict = d.dict_sources.endict ?? 0
  const monthShare = share(d.chars_this_month, d.month_limit)
  const speechShare = share(d.speech_chars_this_month, d.speech_month_limit)

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
      label: 'Англо-русский словарь',
      ok: endict > 0,
      detail: endict ? `${endict.toLocaleString('ru-RU')} статей` : 'не импортирован',
      advice:
        'Нужен только для английских глав: без него карточка слова будет пустой. ' +
        'scripts/import_endict.py <файл.dsl>.',
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
      label: 'Озвучка',
      ok: d.speech_configured,
      detail: d.speech_configured ? `ключ задан, голос ${d.speech_voice}` : 'ключ не задан',
      advice:
        'Без ключа глава читается, но не звучит. Роль нужна своя — ai.speechkit-tts.user: ' +
        'с одной только ai.translate.user синтез отвечает 403.',
      probe: 'speech',
    },
    {
      label: 'Профиль браузера',
      ok: d.browser_profile_exists,
      detail: d.browser_profile_exists ? 'на месте' : 'отсутствует',
      advice: 'Без него загрузка упрётся в проверку сайта: профиль хранит куки.',
      probe: 'browser',
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
    {
      label: 'Расход на озвучку',
      ok: speechShare < 90,
      detail:
        `${d.speech_chars_this_month.toLocaleString('ru-RU')} из ` +
        `${d.speech_month_limit.toLocaleString('ru-RU')} символов (${speechShare}%), ` +
        `в кэше ${(d.tts_cache_bytes / 1024 / 1024).toFixed(1)} МБ`,
      advice: 'Потолок свой, отдельно от перевода: тариф у синтеза другой.',
    },
  ]
}

/**
 * Живая проверка синтеза: единственное здесь, что нельзя узнать чтением.
 *
 * Тратит деньги — восемь символов по тарифу — поэтому запускается нажатием, а
 * не открытием экрана.
 */
function SpeechProbe() {
  const [result, setResult] = useState<SpeechCheck | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    setResult(null)
    try {
      setResult(await api.speechCheck())
    } catch (e) {
      // Отказ проверки — это и есть её результат: показывается там же, где
      // удачный ответ, а не отдельным сообщением над экраном.
      const failure = asApiError(e)
      setResult({ ok: false, kind: failure.kind, detail: failure.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="check__probe">
      <button className="button button--quiet" type="button" onClick={() => void run()} disabled={busy}>
        {busy ? 'Проверяю…' : 'Проверить голосом'}
      </button>
      {result && (
        <span className={result.ok ? 'check__probe-ok' : 'check__advice'}>
          {result.ok ? `Синтез работает: ${result.detail}` : result.detail}
        </span>
      )}
    </div>
  )
}

/**
 * Живая проверка сайта в окне браузера.
 *
 * Обычно челлендж проходится сам, и ответ приходит через пару секунд. Но если
 * Cloudflare показал капчу, нажать на неё может только человек — а до этой
 * кнопки такого случая просто не существовало: глава уходила в `failed` с
 * советом «попробуйте через минуту», который в этом случае не помогает
 * никогда.
 *
 * Окно живёт там же, где браузер: на разработческой машине — на экране, на ВМ
 * — на Xvfb, и смотрят его через VNC. Чтобы увидеть проверку и без VNC, рядом
 * показывается снимок экрана.
 */
function BrowserProbe() {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<BrowserCheck | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  // Файл снимка один и всегда по одному адресу: без метки браузер показал бы
  // прошлую проверку вместо только что запущенной.
  const [stamp, setStamp] = useState(0)

  async function run() {
    const target = url.trim()
    if (!target || busy) return
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const got = await api.browserCheck(target)
      setResult(got)
      setStamp(Date.now())
    } catch (e) {
      setError(asApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="probe">
      <form
        className="probe__form"
        onSubmit={(e) => {
          e.preventDefault()
          void run()
        }}
      >
        <input
          className="input"
          type="url"
          inputMode="url"
          placeholder="https://51shucheng.net/… — адрес, который не открывается"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
          aria-label="Адрес для проверки в браузере"
        />
        <button className="button button--quiet" type="submit" disabled={busy || !url.trim()}>
          {busy ? 'Окно открыто…' : 'Открыть в браузере'}
        </button>
      </form>

      <p className="muted probe__hint">
        Откроет страницу в живом окне браузера и подождёт до минуты. Если сайт показал
        капчу — пройдите её руками в этом окне: на этой машине оно на экране, на сервере
        живёт на Xvfb и смотрится через VNC. Снимок экрана появится здесь в любом случае.
      </p>

      {busy && (
        <p className="muted progress" role="status" aria-live="polite">
          Жду. Обычно проверка проходит сама за несколько секунд.
        </p>
      )}

      {error && <ErrorNote kind={error.kind} detail={error.message} />}

      {result && (
        <>
          <p className={result.ok ? 'check__probe-ok' : 'check__advice'}>
            {describeBrowserCheck(result)}
          </p>
          {result.screenshot && (
            <img
              className="probe__shot"
              src={browserScreenshotUrl(stamp)}
              alt="Снимок экрана браузера на момент проверки"
            />
          )}
        </>
      )}
    </div>
  )
}

/**
 * Выбор темы.
 *
 * Тёмная — основная: читают вечером и подолгу. Но системная настройка и
 * настройка читалки — разные вещи: в списке приложений светлое всё, а читают
 * всё равно в тёмном, и до сих пор добиться этого было нельзя никак.
 *
 * Три состояния, а не два: «как в системе» — это не «светлая», даже когда
 * система светлая прямо сейчас. Выбор переживает перезагрузку, потому что
 * вывести его неоткуда — он существует только потому, что его сделали.
 */
function ThemePicker() {
  const [choice, setChoice] = useState<ThemeChoice>(loadTheme)

  function choose(next: ThemeChoice) {
    setChoice(next)
    saveTheme(next)
    applyTheme(next)
  }

  return (
    <div className="setting">
      <div className="setting__label">Тема</div>
      <div className="switch" role="group" aria-label="Тема оформления">
        {THEME_ORDER.map((option) => (
          <button
            key={option}
            type="button"
            className={`switch__option${option === choice ? ' is-selected' : ''}`}
            aria-pressed={option === choice}
            onClick={() => choose(option)}
          >
            {describeTheme(option)}
          </button>
        ))}
      </div>
    </div>
  )
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
      .catch((e) => setError(asApiError(e)))
  }

  useEffect(load, [])

  // Тема — настройка местная, и она обязана переключаться даже когда сервис
  // лёг: смотреть на белый экран, пока чинишь бэкенд, незачем. Поэтому
  // picker стоит выше ранних возвратов, а не внутри удачной ветки.
  if (error) {
    return (
      <>
        <h1>Состояние</h1>
        <ThemePicker />
        <ErrorNote kind={error.kind} detail={error.message} onRetry={load} />
      </>
    )
  }

  if (!data) {
    return (
      <>
        <h1>Состояние</h1>
        <ThemePicker />
        <p className="muted">Загружаю…</p>
      </>
    )
  }

  const checks = buildChecks(data)
  const broken = checks.filter((c) => !c.ok)

  return (
    <>
      <h1>Состояние</h1>

      <ThemePicker />

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
              {check.probe === 'speech' && <SpeechProbe />}
              {check.probe === 'browser' && <BrowserProbe />}
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
