/**
 * Книги и их оглавления.
 *
 * Появились вместе с обходом цепочкой. Пока глава открывалась по ссылке и была
 * одна, список был не нужен; с «и ещё N глав» их заводится два десятка за
 * запрос — и без оглавления они оказываются записями, к которым нет дороги:
 * адрес знает только сайт, а номер только база.
 *
 * **Название пишет читатель.** Сервис его не выдумывает и не может: `<title>`
 * страницы — это «Глава 12 — Книга | Сайт», а заголовок первой главы — имя
 * главы. Пока названия нет, показывается адрес — это не красиво, зато правда,
 * и по нему книгу узнают, она же по нему и открывалась. Стерев название,
 * читатель к этому показу и возвращается.
 *
 * **Удаление спрашивает один раз и прямо здесь.** Не `confirm()` браузера:
 * на телефоне он выглядит системным окном неизвестно от кого, а сказать в нём,
 * сколько именно глав сейчас исчезнет, нельзя.
 *
 * **Глава без номера стоит в конце и помечена.** `idx` проставляется, только
 * когда положение выведено из цепочки; у главы, открытой отдельной ссылкой в
 * середину книги, его нет. Показать ей выдуманный номер значило бы сделать
 * оглавление, по которому нельзя заметить, что чего-то не хватает.
 *
 * **Выгрузка книги — работа на час, а не запрос.** Поэтому у неё есть
 * прогресс, который спрашивается по таймеру, и она честно говорит, чем
 * кончилась: дошла до конца книги или упёрлась в отказ сайта.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  api,
  isReadable,
  type Book,
  type BookWalk,
  type ChapterBrief,
} from '../api'
import { bookLabel, describeWalk } from '../books'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { hrefFor } from '../router'

/** Как часто спрашивать, сколько уже выгружено. */
const WALK_POLL_MS = 3000

function useLoaded<T>(load: () => Promise<T>): {
  data: T | null
  error: ApiError | null
  reload: () => void
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  const reload = useCallback(() => {
    load()
      .then((value) => {
        setData(value)
        setError(null)
      })
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError('network', String(e))))
  }, [load])

  useEffect(reload, [reload])
  return { data, error, reload }
}

function describeCounts(book: Book): string {
  // Разница между «сколько загружено» и «сколько можно читать» — это ровно те
  // главы, что не дошли: упёрлись в проверку сайта или ещё грузятся.
  const total = `${book.chapters} гл.`
  return book.readable === book.chapters ? total : `${total}, читаемы ${book.readable}`
}

/** Что делают с карточкой прямо сейчас. */
type CardMode = 'idle' | 'renaming' | 'confirming'

function BookCard({ book, onChanged }: { book: Book; onChanged: () => void }) {
  const [mode, setMode] = useState<CardMode>('idle')
  const [draft, setDraft] = useState(book.title ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  async function act(work: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await work()
      setMode('idle')
      onChanged()
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setBusy(false)
    }
  }

  /** Уйти из правки. Сообщение об отказе уходит вместе с ней: оно было про неё. */
  function cancel() {
    setError(null)
    setMode('idle')
  }

  if (mode === 'renaming') {
    return (
      <li className="book book--editing">
        <form
          className="book__edit"
          onSubmit={(e) => {
            e.preventDefault()
            void act(() => api.renameBook(book.id, draft))
          }}
        >
          <input
            className="input"
            value={draft}
            autoFocus
            maxLength={200}
            placeholder={bookLabel(book)}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
            aria-label="Название книги"
          />
          <div className="book__actions">
            <button className="button" type="submit" disabled={busy}>
              {busy ? 'Сохраняю…' : 'Сохранить'}
            </button>
            <button
              className="button button--quiet"
              type="button"
              onClick={() => cancel()}
              disabled={busy}
            >
              Отмена
            </button>
          </div>
        </form>
        <p className="muted book__hint">
          Пустое название вернёт показ по адресу: {book.key}
        </p>
        {error && <ErrorNote kind={error.kind} detail={error.message} />}
      </li>
    )
  }

  if (mode === 'confirming') {
    return (
      <li className="book book--editing">
        <div className="book__confirm">
          <span>
            Удалить «{bookLabel(book)}» и {book.chapters} гл.? Слова из словаря останутся.
          </span>
          <div className="book__actions">
            <button
              className="button button--danger"
              type="button"
              onClick={() => void act(() => api.deleteBook(book.id))}
              disabled={busy}
            >
              {busy ? 'Удаляю…' : 'Удалить'}
            </button>
            <button
              className="button button--quiet"
              type="button"
              onClick={() => cancel()}
              disabled={busy}
            >
              Отмена
            </button>
          </div>
        </div>
        {error && <ErrorNote kind={error.kind} detail={error.message} />}
      </li>
    )
  }

  return (
    <li className="book">
      <a className="book__link" href={hrefFor({ name: 'book', id: book.id })}>
        <span className="book__name">{bookLabel(book)}</span>
        <span className="book__meta muted">
          {book.site} · {book.lang === 'zh' ? 'китайский' : 'английский'}
        </span>
      </a>
      <span className="book__count muted">{describeCounts(book)}</span>
      <div className="book__actions">
        <button
          className="button button--quiet"
          type="button"
          onClick={() => {
            setDraft(book.title ?? '')
            setMode('renaming')
          }}
        >
          Переименовать
        </button>
        <button
          className="button button--quiet"
          type="button"
          onClick={() => setMode('confirming')}
        >
          Удалить
        </button>
      </div>
      {error && <ErrorNote kind={error.kind} detail={error.message} />}
    </li>
  )
}

function BookList() {
  const { data, error, reload } = useLoaded(useCallback(() => api.books(), []))

  if (error) return <ErrorNote kind={error.kind} detail={error.message} onRetry={reload} />
  if (!data) return <p className="muted">Загружаю…</p>

  if (data.length === 0) {
    return (
      <p className="muted">
        Пока пусто. Книги заводятся из чтения: загрузите главу, а вместе с ней — сколько
        нужно следующих.
      </p>
    )
  }

  return (
    <ul className="books">
      {data.map((book) => (
        <BookCard book={book} key={book.id} onChanged={reload} />
      ))}
    </ul>
  )
}

function ChapterRow({ chapter }: { chapter: ChapterBrief }) {
  const readable = isReadable(chapter.status)
  const label = chapter.title || 'без заголовка'
  const number = chapter.idx === null ? '—' : chapter.idx + 1

  const inner = (
    <>
      <span className="toc__number muted">{number}</span>
      <span className="toc__title" lang={chapter.lang === 'zh' ? 'zh-Hans' : 'en'}>
        {label}
      </span>
      {!readable && <span className="toc__state muted">{describeStatus(chapter.status)}</span>}
      {chapter.error && <span className="toc__state toc__state--bad">{chapter.error.kind}</span>}
    </>
  )

  return (
    <li className={`toc__row${readable ? '' : ' toc__row--dim'}`}>
      {readable ? (
        <a className="toc__link" href={hrefFor({ name: 'chapter', id: chapter.id })}>
          {inner}
        </a>
      ) : (
        // Нечитаемую главу незачем делать ссылкой: она откроется пустой, и
        // причина, по которой это случилось, уже написана рядом.
        <span className="toc__link toc__link--dead">{inner}</span>
      )}
    </li>
  )
}

/**
 * Выгрузка книги целиком.
 *
 * Кнопка ставит работу и уходит; дальше экран спрашивает прогресс по таймеру и
 * обновляет вместе с ним оглавление — иначе загруженные главы появлялись бы
 * только после перезагрузки страницы.
 *
 * Галочка перевода выключена, и это не осторожность, а арифметика: 550 глав —
 * это полтора миллиона символов, половина месячного потолка. Текст читается и
 * без перевода, а перевести главу можно кнопкой, когда до неё дойдёт очередь.
 */
function WalkControl({ bookId, onProgress }: { bookId: number; onProgress: () => void }) {
  const [walk, setWalk] = useState<BookWalk | null>(null)
  const [translate, setTranslate] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  // Счётчик опросов. Без него цепочка держится на смене `walk`, и один
  // неудачный запрос останавливает её навсегда: состояние не изменилось —
  // эффект не перезапустился — следующего опроса не будет. А неудачный запрос
  // здесь дело обычное: телефон уходит в сон, вайфай отваливается.
  const [tick, setTick] = useState(0)
  // Обновлять оглавление надо по ходу выгрузки, но зависимостью в таймере
  // колбэк держать нельзя: он приезжает новым на каждый рендер и перезапускал
  // бы опрос вместо того, чтобы дать ему идти.
  const progress = useRef(onProgress)
  progress.current = onProgress

  const poll = useCallback(async () => {
    try {
      setWalk(await api.bookWalk(bookId))
    } catch {
      // Молчим намеренно: это опрос прогресса, а не работа. Отказ одного
      // запроса не повод показывать ошибку поверх идущей выгрузки.
    }
  }, [bookId])

  useEffect(() => {
    void poll()
  }, [poll])

  useEffect(() => {
    if (!walk?.running) return
    const timer = setTimeout(() => {
      progress.current()
      // Счётчик двигается после ответа, а не до: иначе он перезапустил бы
      // эффект раньше, чем приедет состояние, и опросы пошли бы чаще срока.
      void poll().finally(() => setTick((n) => n + 1))
    }, WALK_POLL_MS)
    return () => clearTimeout(timer)
  }, [walk, tick, poll])

  // Выгрузка закончилась — показать её итог вместе с полным оглавлением.
  const running = walk?.running ?? false
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) progress.current()
    wasRunning.current = running
  }, [running])

  async function act(work: () => Promise<BookWalk>) {
    setStarting(true)
    setError(null)
    try {
      setWalk(await work())
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="walk">
      <div className="walk__row">
        <button
          className="button"
          type="button"
          onClick={() => void act(() => api.walkBook(bookId, translate))}
          disabled={starting || running}
        >
          {running ? 'Выгружаю…' : 'Выгрузить всю книгу'}
        </button>

        {/* Пока выгрузка идёт, остановить её — единственное осмысленное
            действие: она может занять час, а ждать его никто не обязан. */}
        {running && (
          <button
            className="button button--quiet"
            type="button"
            onClick={() => void act(() => api.stopBookWalk(bookId))}
            disabled={starting || walk?.cancelled}
          >
            {walk?.cancelled ? 'Останавливаюсь…' : 'Остановить'}
          </button>
        )}

        <label className="walk__option">
          <input
            type="checkbox"
            checked={translate}
            onChange={(e) => setTranslate(e.target.checked)}
            disabled={running}
          />
          <span className="muted">переводить сразу</span>
        </label>
      </div>

      <p className="muted walk__note" role="status" aria-live="polite">
        {describeWalk(walk, translate)}
      </p>

      {error && <ErrorNote kind={error.kind} detail={error.message} />}
    </div>
  )
}

function TableOfContents({ id }: { id: number }) {
  const { data, error, reload } = useLoaded(useCallback(() => api.bookChapters(id), [id]))

  return (
    <>
      {/* Выгрузка стоит выше оглавления и не зависит от него. Иначе один
          неудачный запрос списка глав — телефон ушёл в сон, вайфай отвалился —
          убирал бы с экрана кнопку «Остановить» посреди часовой работы. */}
      <WalkControl bookId={id} onProgress={reload} />

      {error ? (
        <ErrorNote kind={error.kind} detail={error.message} onRetry={reload} />
      ) : !data ? (
        <p className="muted">Загружаю…</p>
      ) : (
        <>
          <ul className="toc">
            {data.map((chapter) => (
              <ChapterRow chapter={chapter} key={chapter.id} />
            ))}
          </ul>
          {data.some((chapter) => chapter.idx === null) && (
            <p className="muted check__footer">
              Главы с прочерком открыты отдельной ссылкой: их место в книге вывести неоткуда.
            </p>
          )}
        </>
      )}
    </>
  )
}

/** Заголовок экрана книги: как она называется, а не «Оглавление» вообще. */
function BookHeading({ id }: { id: number }) {
  const { data } = useLoaded(useCallback(() => api.books(), []))
  const book = data?.find((candidate) => candidate.id === id) ?? null

  return <h1>{book ? bookLabel(book) : 'Оглавление'}</h1>
}

export function BooksScreen({ id }: { id?: number }) {
  if (id === undefined) {
    return (
      <>
        <h1>Книги</h1>
        <BookList />
      </>
    )
  }

  return (
    <>
      <BookHeading id={id} />
      <TableOfContents id={id} />
      <p className="check__footer">
        <a href={hrefFor({ name: 'books' })}>← Все книги</a>
      </p>
    </>
  )
}
