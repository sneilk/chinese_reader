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
 * Книга в списке. Заголовка у неё нет и он не выдумывается: `<title>` страницы
 * — это «Глава 12 — Книга | Сайт», а заголовок первой главы — имя главы, и
 * подставить второе под первое значит назвать книгу именем её двенадцатой
 * главы. Наружу едет адрес; как показать его человеку, решает интерфейс.
 */
export interface Book {
  id: number
  key: string
  lang: Language
  site: string | null
  chapters: number
  /** Сколько глав уже можно открыть и читать. */
  readable: number
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
  limits: { max_chapters_per_run: number }
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
