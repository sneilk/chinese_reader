/**
 * Тонкий клиент API (RFC §9): ни Redux, ни react-query — экранов три.
 *
 * Типы повторяют схемы бэкенда из `app/api/schemas.py`. Держать их руками
 * дешевле, чем заводить кодогенерацию на семь полей, но при правке схемы
 * править надо оба места.
 */

export type ChapterStatus =
  | 'fetching'
  | 'segmented'
  | 'translating'
  | 'ready'
  | 'failed'

/**
 * Язык оригинала главы. Русского здесь нет: это язык перевода, а не чтения.
 *
 * От него зависят гарнитура текста, язык запроса к словарю и язык, под которым
 * слово ляжет в личный словарь. Угадывать его по содержимому фронт не должен —
 * он приезжает с главой.
 */
export type Language = 'zh' | 'en'

/** Что показывает ридер: оригинал с разбором по словам или русский перевод. */
export type ReadingMode = 'source' | 'translation'

/** Причины отказа из RFC §4. Фронт разбирает их по `kind`, а не по тексту. */
export type ErrorKind =
  | 'challenge'
  | 'not_found'
  | 'empty_extract'
  | 'fetch_timeout'
  | 'adapter_error'
  | 'translate_failed'
  | 'speech_failed'
  | 'interrupted'
  | 'budget_exceeded'
  | 'bad_request'

export interface ApiErrorBody {
  kind: ErrorKind | string
  message: string
}

/** Токен главы: [начало, конец, род]. Офсеты — по `content`. */
export type Token = [number, number, TokenKind]

export type TokenKind = 'word' | 'punct' | 'latin' | 'digit' | 'space'

export interface Sentence {
  id: number
  idx: number
  start: number
  end: number
  translation: string | null
}

export interface WordContext {
  sentence: string
  offset_start: number
  offset_end: number
  chapter_id?: number | null
  sentence_id?: number | null
}

export interface UserWord {
  id: number
  lang: string
  headword: string
  reading: string | null
  user_translation: string | null
  note: string | null
  added_at: string
  contexts: (WordContext & { created_at: string })[]
}

export interface WordsPage {
  items: UserWord[]
  total: number
}

/** Состояние сервиса. Ключей и адресов здесь нет — только факт настройки. */
export interface Diagnostics {
  version: string
  schema_revision: string | null
  db_size_bytes: number
  chapters: number
  sentences: number
  user_words: number
  dict_entries: number
  dict_sources: Record<string, number>
  userdict_words: number
  translator_configured: boolean
  chars_this_month: number
  month_limit: number
  speech_configured: boolean
  speech_voice: string
  speech_chars_this_month: number
  speech_month_limit: number
  tts_cache_bytes: number
  browser_profile_exists: boolean
  browser_headless: boolean
}

export interface Chapter {
  id: number
  url: string
  title: string | null
  lang: Language
  status: ChapterStatus
  error: ApiErrorBody | null
  content: string | null
  tokens: Token[]
  sentences: Sentence[]
  chars_sent: number
  /** Адрес следующей главы на сайте — есть он или нет, решает адаптер. */
  next_url: string | null
  /** Она же, если уже загружена: тогда переход бесплатный, без похода на сайт. */
  next_chapter_id: number | null
}

export interface ChapterAccepted {
  id: number
  status: ChapterStatus
  created: boolean
}

/**
 * Книга в списке. Заголовок сервис не выдумывает: `<title>` страницы — это
 * «Глава 12 — Книга | Сайт», а заголовок первой главы — имя главы, и
 * подставить второе под первое значит назвать книгу именем её двенадцатой
 * главы. Поэтому наружу едет адрес, а название — то, что написал читатель.
 */
export interface Book {
  id: number
  key: string
  /** Название, написанное читателем. `null` — показываем адрес. */
  title: string | null
  lang: Language
  site: string | null
  chapters: number
  /** Сколько глав уже можно открыть и читать. */
  readable: number
}

/**
 * Состояние выгрузки книги целиком.
 *
 * У книги-образца 550 глав по две секунды паузы — это час работы с одного
 * нажатия, и без состояния он неотличим от зависшего сервиса.
 */
export interface BookWalk {
  book_id: number
  running: boolean
  /** Сколько глав загружено с начала этой выгрузки. */
  loaded: number
  limit: number
  /** Отказ, оборвавший выгрузку. `null` — дошли до конца книги или до потолка. */
  stopped_by: string | null
  /** Выгрузку попросили прекратить. Это не отказ: загруженное на месте. */
  cancelled: boolean
}

/** Что оказалось на странице, открытой в живом окне браузера. */
export interface BrowserCheck {
  ok: boolean
  kind: string | null
  status: number
  title: string
  url: string
  waited_seconds: number
  /** Видно ли окно человеку. `false` — браузер headless, проходить нечего. */
  visible: boolean
  screenshot: boolean
}

/** Глава в оглавлении: всё, что нужно, чтобы выбрать и открыть. Без текста. */
export interface ChapterBrief {
  id: number
  /** Место в цепочке; `null` — глава загружена отдельной ссылкой в середину. */
  idx: number | null
  title: string | null
  lang: Language
  status: ChapterStatus
  error: ApiErrorBody | null
}

/** Пределы, которые сервер объявляет клиенту, чтобы тот не предлагал невозможного. */
export interface Health {
  status: string
  limits: {
    /** Сколько глав можно попросить пройти вперёд с экрана главы. */
    max_chapters_per_run: number
    /** Где остановится выгрузка книги целиком. */
    max_chapters_per_book: number
  }
}

/** Итог живой проверки синтеза: единственное, что нельзя узнать чтением настроек. */
export interface SpeechCheck {
  ok: boolean
  kind: string | null
  detail: string
}

export interface DictEntry {
  headword: string
  traditional: string | null
  reading: string | null
  senses: string[]
  source: string
}

/** Значение одного знака — для слова, которого в словаре нет. */
export interface CharGloss {
  char: string
  reading: string | null
  senses: string[]
}

export interface Lookup {
  word: string
  found: boolean
  /** Карточка собрана из знаков, а не из статьи о слове целиком. */
  approximate: boolean
  /**
   * Форма, под которой слово нашлось: `running` найдено как `run`. `null` —
   * совпало как есть. Показывать обязательно, иначе карточка выглядит так,
   * будто в словаре стоит ровно то, что в тексте.
   */
  matched: string | null
  entries: DictEntry[]
  chars: CharGloss[]
}

/** Ошибка запроса с разобранным `kind` — на нём строятся сообщения экрана. */
export class ApiError extends Error {
  readonly kind: string

  constructor(kind: string, message: string) {
    super(message || kind)
    this.name = 'ApiError'
    this.kind = kind
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      headers: { 'content-type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    // Сеть отвалилась до ответа: сервер спит, телефон потерял вайфай.
    throw new ApiError('network', String(cause))
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: ApiErrorBody
    } | null
    const error = body?.error
    throw new ApiError(error?.kind ?? `http_${response.status}`, error?.message ?? '')
  }

  // 204 приходит без тела — на DELETE это норма, а не сбой разбора.
  if (response.status === 204) return undefined as T

  return (await response.json()) as T
}

export const api = {
  /** Живость и объявленные сервером пределы. Дёшево: это не диагностика. */
  health: () => request<Health>('/health'),

  /** Книги со счётчиками глав, свежие сверху. */
  books: () => request<Book[]>('/books'),

  /** Оглавление книги в порядке чтения. Без текста глав. */
  bookChapters: (bookId: number) => request<ChapterBrief[]>(`/books/${bookId}/chapters`),

  /** Назвать книгу. Пустая строка возвращает показ по адресу. */
  renameBook: (bookId: number, title: string) =>
    request<Book>(`/books/${bookId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  /** Удалить книгу вместе с главами. Слова из словаря это не трогает. */
  deleteBook: (bookId: number) => request<void>(`/books/${bookId}`, { method: 'DELETE' }),

  /**
   * Выгрузить книгу целиком: идти вперёд, пока идётся.
   *
   * Перевод выключен по умолчанию, и это про деньги: книга-образец — полтора
   * миллиона символов, половина месячного потолка.
   */
  walkBook: (bookId: number, translate = false) =>
    request<BookWalk>(`/books/${bookId}/walk`, {
      method: 'POST',
      body: JSON.stringify({ translate }),
    }),

  /** Сколько уже выгружено. Спрашивается, пока выгрузка идёт. */
  bookWalk: (bookId: number) => request<BookWalk>(`/books/${bookId}/walk`),

  /**
   * Прекратить выгрузку. Загруженное остаётся, продолжить можно той же кнопкой.
   *
   * Работа не убивается на середине: сервис смотрит просьбу между главами и
   * уходит, дописав текущую, — поэтому `running` гаснет через секунду-две.
   */
  stopBookWalk: (bookId: number) =>
    request<BookWalk>(`/books/${bookId}/walk`, { method: 'DELETE' }),

  /**
   * Поставить главу в очередь. Идемпотентно: повтор не ходит на сайт.
   *
   * `follow` продолжает работу за границу главы — конвейер пойдёт вперёд по
   * ссылкам «следующая глава». Уже загруженные он перешагивает, не трогая
   * сайт, поэтому повтор «догрузи ещё» приносит именно новые главы.
   */
  createChapter: (url: string, follow = 0) =>
    request<ChapterAccepted>('/chapters', {
      method: 'POST',
      body: JSON.stringify({ url, follow }),
    }),

  getChapter: (id: number) => request<Chapter>(`/chapters/${id}`),

  /** Дозалить перевод после отказа. Переведённое не переотправляется. */
  translateChapter: (id: number) =>
    request<ChapterAccepted>(`/chapters/${id}/translate`, { method: 'POST' }),

  /**
   * Спросить у сайта заново, куда ведёт глава.
   *
   * Нужно главам, загруженным до того, как адаптер научился читать ссылку
   * вперёд: у них её нет и не появится само. Текст и переводы при этом не
   * трогаются — со страницы берётся только ссылка.
   */
  relinkChapter: (id: number) => request<Chapter>(`/chapters/${id}/relink`, { method: 'POST' }),

  /** Значения слова из локальных словарей: без интернета и без задержки. */
  lookup: (word: string, lang: Language = 'zh') =>
    request<Lookup>(`/lookup?word=${encodeURIComponent(word)}&lang=${lang}`),

  /**
   * Сохранить слово с контекстом. Это же и правка границ: с этого момента
   * сегментатор режет слово целиком (T2.7).
   */
  saveWord: (body: {
    headword: string
    lang?: string
    reading?: string | null
    user_translation?: string | null
    note?: string | null
    context?: WordContext
  }) => request<UserWord>('/words', { method: 'POST', body: JSON.stringify(body) }),

  listWords: (params: { query?: string; limit?: number; offset?: number } = {}) => {
    const search = new URLSearchParams()
    if (params.query) search.set('query', params.query)
    if (params.limit) search.set('limit', String(params.limit))
    if (params.offset) search.set('offset', String(params.offset))
    const tail = search.toString()
    return request<WordsPage>(`/words${tail ? `?${tail}` : ''}`)
  },

  updateWord: (id: number, body: { reading?: string; user_translation?: string; note?: string }) =>
    request<UserWord>(`/words/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  deleteWord: (id: number) => request<void>(`/words/${id}`, { method: 'DELETE' }),

  /** Состояние сервиса: что настроено, а что нет. */
  diagnostics: () => request<Diagnostics>('/diagnostics'),

  /**
   * Озвучить одно слово и сказать, что вышло. Тратит деньги, поэтому POST и
   * только по нажатию: «ключ задан» горит зелёным и там, где не хватает роли.
   */
  speechCheck: () => request<SpeechCheck>('/diagnostics/speech-check', { method: 'POST' }),

  /**
   * Открыть страницу в живом окне браузера и подождать, пока проверку пройдут.
   *
   * Обычно челлендж проходится сам, и ответ приходит через пару секунд. Но
   * если сайт показал капчу, нажать на неё может только человек — и это
   * единственный способ до неё добраться.
   */
  browserCheck: (url: string, seconds = 60) =>
    request<BrowserCheck>('/diagnostics/browser-check', {
      method: 'POST',
      body: JSON.stringify({ url, seconds }),
    }),
}

/**
 * Адрес снимка экрана последней проверки. Не запрос, а ссылка: её ставит
 * `<img>`. Метка времени в строке запроса обязательна — файл один и всегда по
 * одному адресу, и без неё браузер показал бы прошлую проверку.
 */
export function browserScreenshotUrl(stamp: number): string {
  return `/api/diagnostics/browser-check/screenshot?t=${stamp}`
}

/**
 * Адрес озвучки предложения. Не запрос, а ссылка: её ставит `<audio>` и
 * дальше сам решает, когда качать, сколько буферизовать и как перематывать.
 * Отдать сюда байты через `fetch` значило бы отобрать у него всё это.
 */
export function audioUrl(chapterId: number, sentenceIdx: number): string {
  return `/api/chapters/${chapterId}/audio/${sentenceIdx}`
}

/** Глава читаема начиная с `segmented`: текст и токены уже есть. */
export function isReadable(status: ChapterStatus): boolean {
  return status === 'segmented' || status === 'translating' || status === 'ready'
}

/** Работа ещё идёт — значит имеет смысл опрашивать статус дальше. */
export function isPending(status: ChapterStatus): boolean {
  return status === 'fetching' || status === 'translating'
}
