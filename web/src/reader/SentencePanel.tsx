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
 */

import type { Lookup, Sentence } from '../api'
import type { Selected } from './useSelection'

const GRANULARITY: Record<Selected['granularity'], string> = {
  token: 'слово',
  phrase: 'фраза',
  char: 'знак',
}

function Entries({ lookup }: { lookup: Lookup }) {
  return (
    <>
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

export function SentencePanel({
  selected,
  sentence,
  lookup,
  onClose,
}: {
  selected: Selected
  sentence: Sentence | null
  lookup: Lookup | null
  onClose: () => void
}) {
  const reading = lookup?.entries[0]?.reading

  return (
    <aside className="panel" aria-live="polite">
      <div className="panel__inner">
        <div className="panel__head">
          <span className="panel__term" lang="zh-Hans">
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
        </div>
      </div>
    </aside>
  )
}
