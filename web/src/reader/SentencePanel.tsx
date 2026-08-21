/**
 * Нижняя панель: карточка выделенного и перевод его предложения (T1.16, T2.5).
 *
 * Не модальное окно и не оверлей — намеренно. Чтение не должно прерываться:
 * текст остаётся на месте и продолжает прокручиваться, панель лишь занимает
 * полосу внизу. Модалка же требует закрыть себя перед следующим словом, а
 * слов за главу — сотни.
 *
 * Перевод предложения показывается сразу и без сети: глава переводится целиком
 * при загрузке (translation.md §3). Значения слова подтягиваются из локальных
 * словарей отдельно — они приходят быстро, но всё же запросом, и панель не
 * должна их ждать.
 *
 * Когда статьи на слово нет — а для имён героев это норма, — карточка
 * собирается из отдельных знаков и **помечается явно**: сумма значений знаков
 * не равна переводу слова, и выдавать одно за другое нельзя.
 *
 * Сужение до одного знака (segmentation.md §4) живёт здесь, а не в жестах:
 * удержание теперь показывает слово целиком, и отдельного касания под разбор
 * по знакам не осталось. Ряд знаков под заголовком честнее прежнего долгого
 * тапа — он показывает, из чего слово состоит, ещё до того, как по нему попали.
 *
 * ## Английское слово разбирается не по буквам, а по формам
 *
 * Ряда знаков в английском режиме нет: буква ничего не значит, и предлагать
 * ткнуть в неё — предлагать бессмыслицу. Вместо него показывается **форма, под
 * которой слово нашлось**: в тексте `running`, в словаре `run`. Без этой
 * пометки карточка выглядит так, будто словарь знает ровно то слово, которое
 * стоит в тексте, — а он знает другое, и на `saw` эта разница решает всё.
 */

import type { Language, Lookup, Sentence } from '../api'
import { splitChars } from './tokens'
import type { Selected } from './useSelection'

const GRANULARITY: Record<Selected['granularity'], string> = {
  token: 'слово',
  phrase: 'фраза',
  char: 'знак',
}

/** Состояние кнопки «в словарь»: она же — правка границ (T2.7). */
export type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

const SAVE_LABEL: Record<SaveState, string> = {
  idle: 'В словарь',
  saving: 'Сохраняю…',
  saved: 'В словаре',
  failed: 'Не вышло, ещё раз',
}

function Entries({ lookup }: { lookup: Lookup }) {
  return (
    <>
      {lookup.matched && (
        <p className="card__note muted">
          Статья на начальную форму — <b>{lookup.matched}</b>.
        </p>
      )}
      {lookup.entries.map((entry, i) => (
        <div className="card__entry" key={`${entry.source}-${i}`}>
          <div className="card__meta">
            {entry.reading && <span className="card__reading">{entry.reading}</span>}
            <span className="card__source muted">{entry.source}</span>
          </div>
          <ol className="card__senses">
            {entry.senses.slice(0, 6).map((sense, k) => (
              <li key={k}>{sense}</li>
            ))}
          </ol>
        </div>
      ))}
    </>
  )
}

function CharGlosses({ lookup }: { lookup: Lookup }) {
  return (
    <div className="card__entry">
      <p className="card__note muted">
        Статьи на это слово нет — вот значения знаков по отдельности.
      </p>
      <ul className="card__chars">
        {lookup.chars.map((gloss, i) => (
          <li className="card__char" key={`${gloss.char}-${i}`}>
            <span className="card__char-han" lang="zh-Hans">
              {gloss.char}
            </span>
            <span className="card__char-reading">{gloss.reading ?? '—'}</span>
            <span className="card__char-sense muted">
              {gloss.senses.length ? gloss.senses.join('; ') : 'нет значения'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Ряд знаков выделенного слова: тап по знаку сужает карточку до него, тап по
 * уже выбранному возвращает к слову целиком.
 */
function CharPicker({
  token,
  charOffset,
  onNarrow,
}: {
  token: { text: string; start: number }
  charOffset: number | null
  onNarrow: (charOffset: number | null) => void
}) {
  return (
    <div className="panel__chars" role="group" aria-label="Знаки слова">
      {splitChars(token.text, token.start).map((piece) => {
        const active = piece.start === charOffset
        return (
          <button
            className={`panel__char${active ? ' is-selected' : ''}`}
            key={piece.start}
            type="button"
            lang="zh-Hans"
            aria-pressed={active}
            onClick={() => onNarrow(active ? null : piece.start)}
          >
            {piece.text}
          </button>
        )
      })}
    </div>
  )
}

export function SentencePanel({
  selected,
  sentence,
  lookup,
  token,
  lang,
  charOffset,
  onNarrow,
  saveState,
  onSave,
  onClose,
}: {
  selected: Selected
  sentence: Sentence | null
  lookup: Lookup | null
  /** Выделенный токен целиком — основа для разбора по знакам. */
  token: { text: string; start: number } | null
  lang: Language
  charOffset: number | null
  onNarrow: (charOffset: number | null) => void
  saveState: SaveState
  onSave: () => void
  onClose: () => void
}) {
  const reading = lookup?.entries[0]?.reading
  // Односложному слову разбирать нечего: ряд из одного знака только шумит.
  // В английском не нужен вовсе — буква не единица смысла.
  const chars =
    lang === 'zh' && token && splitChars(token.text, token.start).length > 1 ? token : null

  return (
    <aside className="panel" aria-live="polite">
      <div className="panel__inner">
        <div className="panel__head">
          <span className="panel__term" lang={lang === 'zh' ? 'zh-Hans' : 'en'}>
            {selected.text}
          </span>
          {reading && <span className="panel__reading">{reading}</span>}
          <span className="panel__kind muted">{GRANULARITY[selected.granularity]}</span>
          <button
            className="panel__close"
            type="button"
            onClick={onClose}
            aria-label="Закрыть панель"
          >
            ✕
          </button>
        </div>

        {chars && (
          <CharPicker token={chars} charOffset={charOffset} onNarrow={onNarrow} />
        )}

        <div className="panel__body">
          {sentence?.translation ? (
            <p className="panel__translation">{sentence.translation}</p>
          ) : (
            <p className="panel__translation muted">
              {sentence ? 'Перевода этого предложения пока нет.' : 'Предложение не определилось.'}
            </p>
          )}

          {lookup?.found && <Entries lookup={lookup} />}
          {lookup?.approximate && <CharGlosses lookup={lookup} />}
          {lookup && !lookup.found && !lookup.approximate && (
            <p className="card__note muted">В словарях этого нет.</p>
          )}

          {/* Сохранение — оно же правка границ: с этого момента сегментатор
              режет выделенное целиком, и в следующей главе имя героя не
              рассыплется (segmentation.md §5). */}
          <div className="panel__actions">
            <button
              className="button button--quiet"
              type="button"
              onClick={onSave}
              disabled={saveState === 'saving' || saveState === 'saved'}
            >
              {SAVE_LABEL[saveState]}
            </button>
            {saveState === 'saved' && (
              <span className="muted panel__hint">Дальше режется целиком</span>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
