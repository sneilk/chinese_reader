/**
 * Книги и их оглавления.
 *
 * Появились вместе с обходом цепочкой. Пока глава открывалась по ссылке и была
 * одна, список был не нужен; с «и ещё N глав» их заводится два десятка за
 * запрос — и без оглавления они оказываются записями, к которым нет дороги:
 * адрес знает только сайт, а номер только база.
 *
 * **Заголовка у книги нет, и он не выдумывается.** Взять его неоткуда:
 * `<title>` страницы — это «Глава 12 — Книга | Сайт», а заголовок первой главы
 * — имя главы. Поэтому показывается адрес: слаг из ссылки и имя сайта. Это не
 * красиво, зато правда, и по нему книгу узнают — она же по нему и открывалась.
 *
 * **Глава без номера стоит в конце и помечена.** `idx` проставляется, только
 * когда положение выведено из цепочки; у главы, открытой отдельной ссылкой в
 * середину книги, его нет. Показать ей выдуманный номер значило бы сделать
 * оглавление, по которому нельзя заметить, что чего-то не хватает.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, isReadable, type Book, type ChapterBrief } from '../api'
import { bookName } from '../books'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { hrefFor } from '../router'

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
        <li className="book" key={book.id}>
          <a className="book__link" href={hrefFor({ name: 'book', id: book.id })}>
            <span className="book__name">{bookName(book.key)}</span>
            <span className="book__meta muted">
              {book.site} · {book.lang === 'zh' ? 'китайский' : 'английский'}
            </span>
          </a>
          <span className="book__count muted">{describeCounts(book)}</span>
        </li>
      ))}
    </ul>
  )
}

function describeCounts(book: Book): string {
  // Разница между «сколько загружено» и «сколько можно читать» — это ровно те
  // главы, что не дошли: упёрлись в проверку сайта или ещё грузятся.
  const total = `${book.chapters} гл.`
  return book.readable === book.chapters ? total : `${total}, читаемы ${book.readable}`
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

function TableOfContents({ id }: { id: number }) {
  const { data, error, reload } = useLoaded(useCallback(() => api.bookChapters(id), [id]))

  if (error) return <ErrorNote kind={error.kind} detail={error.message} onRetry={reload} />
  if (!data) return <p className="muted">Загружаю…</p>

  const unplaced = data.some((chapter) => chapter.idx === null)

  return (
    <>
      <ul className="toc">
        {data.map((chapter) => (
          <ChapterRow chapter={chapter} key={chapter.id} />
        ))}
      </ul>
      {unplaced && (
        <p className="muted check__footer">
          Главы с прочерком открыты отдельной ссылкой: их место в книге вывести неоткуда.
        </p>
      )}
    </>
  )
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
      <h1>Оглавление</h1>
      <TableOfContents id={id} />
      <p className="check__footer">
        <a href={hrefFor({ name: 'books' })}>← Все книги</a>
      </p>
    </>
  )
}
