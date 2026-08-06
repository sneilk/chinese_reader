/**
 * Нижняя панель: перевод предложения, в котором стоит выделение (T1.16).
 *
 * Не модальное окно и не оверлей — намеренно. Чтение не должно прерываться:
 * текст остаётся на месте и продолжает прокручиваться, панель лишь занимает
 * полосу внизу. Модалка же требует закрыть себя перед следующим словом, а
 * слов за главу — сотни.
 *
 * Сети здесь нет вовсе: глава переводится целиком при загрузке
 * (translation.md §3), поэтому перевод любого предложения уже лежит рядом.
 * Отсюда и требование «появляется мгновенно» — оно про отсутствие запроса,
 * а не про его скорость.
 *
 * Значения слова, пиньинь и своя карточка появятся здесь же в T2.5.
 */

import type { Sentence } from '../api'
import type { Selected } from './useSelection'

const GRANULARITY: Record<Selected['granularity'], string> = {
  token: 'слово',
  phrase: 'фраза',
  char: 'знак',
}

export function SentencePanel({
  selected,
  sentence,
  onClose,
}: {
  selected: Selected
  sentence: Sentence | null
  onClose: () => void
}) {
  return (
    <aside className="panel" aria-live="polite">
      <div className="panel__inner">
        <div className="panel__head">
          <span className="panel__term" lang="zh-Hans">
            {selected.text}
          </span>
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

        {sentence?.translation ? (
          <p className="panel__translation">{sentence.translation}</p>
        ) : (
          <p className="panel__translation muted">
            {sentence ? 'Перевода этого предложения пока нет.' : 'Предложение не определилось.'}
          </p>
        )}
      </div>
    </aside>
  )
}
