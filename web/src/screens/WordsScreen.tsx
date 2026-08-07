/**
 * Экран личного словаря (T2.8).
 *
 * Критерий задачи прямой: «ни одной причины лезть в SQLite руками». Поэтому
 * здесь есть и правка своих полей, и удаление ошибочной записи — без них
 * первая же опечатка отправляла бы читателя в консоль.
 *
 * Правка идёт по месту, без отдельной формы: карточка словаря — это две
 * строки текста, и открывать ради них экран редактирования незачем. Кнопка
 * сохранения появляется только когда есть что сохранять.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, type UserWord } from '../api'
import { ErrorNote } from '../components/ErrorNote'

const PAGE = 50

/** Разрез по кодовым точкам: офсеты контекста приходят в них, а не в UTF-16. */
function highlight(sentence: string, start: number, end: number) {
  const chars = Array.from(sentence)
  return {
    before: chars.slice(0, start).join(''),
    word: chars.slice(start, end).join(''),
    after: chars.slice(end).join(''),
  }
}

function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
}

function WordCard({
  word,
  onSaved,
  onDeleted,
}: {
  word: UserWord
  onSaved: (word: UserWord) => void
  onDeleted: (id: number) => void
}) {
  const [translation, setTranslation] = useState(word.user_translation ?? '')
  const [note, setNote] = useState(word.note ?? '')
  const [busy, setBusy] = useState(false)

  const dirty = translation !== (word.user_translation ?? '') || note !== (word.note ?? '')
  const context = word.contexts[0]

  async function save() {
    setBusy(true)
    try {
      onSaved(await api.updateWord(word.id, { user_translation: translation, note }))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await api.deleteWord(word.id)
      onDeleted(word.id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="word">
      <div className="word__head">
        <span className="word__han" lang="zh-Hans">
          {word.headword}
        </span>
        {word.reading && <span className="word__reading">{word.reading}</span>}
        <span className="word__date muted">{formatDate(word.added_at)}</span>
      </div>

      {context && (
        <p className="word__context" lang="zh-Hans">
          {(() => {
            const parts = highlight(context.sentence, context.offset_start, context.offset_end)
            return (
              <>
                {parts.before}
                <mark className="word__mark">{parts.word}</mark>
                {parts.after}
              </>
            )
          })()}
        </p>
      )}

      <input
        className="input input--inline"
        value={translation}
        placeholder="свой перевод"
        onChange={(e) => setTranslation(e.target.value)}
        disabled={busy}
      />
      <input
        className="input input--inline"
        value={note}
        placeholder="заметка"
        onChange={(e) => setNote(e.target.value)}
        disabled={busy}
      />

      <div className="word__actions">
        {dirty && (
          <button className="button" type="button" onClick={() => void save()} disabled={busy}>
            Сохранить
          </button>
        )}
        <button
          className="button button--quiet word__delete"
          type="button"
          onClick={() => void remove()}
          disabled={busy}
        >
          Удалить
        </button>
      </div>
    </li>
  )
}

export function WordsScreen() {
  const [items, setItems] = useState<UserWord[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const requestId = useRef(0)

  const load = useCallback(async (search: string, offset: number) => {
    const mine = ++requestId.current
    setLoading(true)
    try {
      const page = await api.listWords({ query: search || undefined, limit: PAGE, offset })
      // Пока ждали ответ, читатель мог набрать ещё букву — чужой ответ не наш.
      if (mine !== requestId.current) return
      setItems((prev) => (offset ? [...prev, ...page.items] : page.items))
      setTotal(page.total)
      setError(null)
    } catch (e) {
      if (mine === requestId.current) {
        setError(e instanceof ApiError ? e : new ApiError('network', String(e)))
      }
    } finally {
      if (mine === requestId.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Пауза перед запросом: словарь ищут набором, а не по букве за запрос.
    const timer = setTimeout(() => void load(query, 0), query ? 250 : 0)
    return () => clearTimeout(timer)
  }, [query, load])

  return (
    <>
      <h1>Словарь</h1>

      <div className="field">
        <input
          className="input"
          type="search"
          placeholder="Поиск по слову или переводу"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {error && <ErrorNote kind={error.kind} detail={error.message} onRetry={() => void load(query, 0)} />}

      {!error && !loading && items.length === 0 && (
        <p className="muted">
          {query
            ? 'Ничего не нашлось.'
            : 'Пока пусто. Слова попадают сюда из чтения — тапом по слову и кнопкой «В словарь».'}
        </p>
      )}

      {items.length > 0 && (
        <p className="muted">
          Слов: {total}
          {items.length < total ? `, показано ${items.length}` : ''}
        </p>
      )}

      <ul className="words">
        {items.map((word) => (
          <WordCard
            key={word.id}
            word={word}
            onSaved={(updated) =>
              setItems((prev) => prev.map((w) => (w.id === updated.id ? updated : w)))
            }
            onDeleted={(id) => {
              setItems((prev) => prev.filter((w) => w.id !== id))
              setTotal((t) => Math.max(0, t - 1))
            }}
          />
        ))}
      </ul>

      {items.length < total && (
        <button
          className="button button--quiet"
          type="button"
          onClick={() => void load(query, items.length)}
          disabled={loading}
        >
          {loading ? 'Загружаю…' : 'Показать ещё'}
        </button>
      )}
    </>
  )
}
