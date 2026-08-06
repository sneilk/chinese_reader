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

/** Причины отказа из RFC §4. Фронт разбирает их по `kind`, а не по тексту. */
export type ErrorKind =
  | 'challenge'
  | 'not_found'
  | 'empty_extract'
  | 'fetch_timeout'
  | 'adapter_error'
  | 'translate_failed'
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
  idx: number
  start: number
  end: number
  translation: string | null
}

export interface Chapter {
  id: number
  url: string
  title: string | null
  status: ChapterStatus
  error: ApiErrorBody | null
  content: string | null
  tokens: Token[]
  sentences: Sentence[]
  chars_sent: number
}

export interface ChapterAccepted {
  id: number
  status: ChapterStatus
  created: boolean
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

  return (await response.json()) as T
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  /** Поставить главу в очередь. Идемпотентно: повтор не ходит на сайт. */
  createChapter: (url: string) =>
    request<ChapterAccepted>('/chapters', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  getChapter: (id: number) => request<Chapter>(`/chapters/${id}`),

  /** Дозалить перевод после отказа. Переведённое не переотправляется. */
  translateChapter: (id: number) =>
    request<ChapterAccepted>(`/chapters/${id}/translate`, { method: 'POST' }),
}

/** Глава читаема начиная с `segmented`: текст и токены уже есть. */
export function isReadable(status: ChapterStatus): boolean {
  return status === 'segmented' || status === 'translating' || status === 'ready'
}

/** Работа ещё идёт — значит имеет смысл опрашивать статус дальше. */
export function isPending(status: ChapterStatus): boolean {
  return status === 'fetching' || status === 'translating'
}
