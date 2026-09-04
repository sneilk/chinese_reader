/**
 * Тонкий клиент API.
 *
 * Сети здесь нет: `fetch` подменяется целиком. Проверяется то, что клиент
 * делает **сам**, — форма запроса и разбор отказа, — а не доступность бэкенда.
 *
 * Главное свойство: у любого отказа наружу уходит `ApiError` с разобранным
 * `kind`. На нём стоят все сообщения экрана (`errors.ts`), и если хоть один
 * путь выпустит наружу голый `TypeError` из fetch или `SyntaxError` из разбора
 * тела, экран покажет «непонятная ошибка» ровно в тот момент, когда объяснение
 * нужнее всего.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  api,
  audioUrl,
  browserScreenshotUrl,
  isPending,
  isReadable,
  type ChapterStatus,
} from './api'

interface Call {
  url: string
  init: RequestInit | undefined
}

/** Подменить `fetch` заданным ответом и записать, с чем его позвали. */
function stub(response: Response | Error): Call[] {
  const calls: Call[] = []
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    return response instanceof Error ? Promise.reject(response) : Promise.resolve(response)
  })
  return calls
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

// --- форма запроса ---

describe('форма запроса', () => {
  it('обращается к /api и просит JSON', async () => {
    const calls = stub(json({ status: 'ok' }))
    await api.health()

    const headers = calls[0].init?.headers as Record<string, string> | undefined
    expect(calls[0].url).toBe('/api/health')
    expect(headers?.['content-type']).toBe('application/json')
  })

  it('глава ставится в очередь POST-ом с адресом и обходом', async () => {
    const calls = stub(json({ id: 1, status: 'fetching', created: true }))
    await api.createChapter('https://example.com/1', 5)

    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      url: 'https://example.com/1',
      follow: 5,
    })
  })

  it('обход по умолчанию выключен', async () => {
    // Загрузить книгу целиком — решение читателя, а не побочное действие ссылки.
    const calls = stub(json({ id: 1, status: 'fetching', created: true }))
    await api.createChapter('https://example.com/1')

    expect(JSON.parse(String(calls[0].init?.body)).follow).toBe(0)
  })

  it('слово и язык уезжают в поиск экранированными', async () => {
    const calls = stub(json({ word: 'a', found: false, approximate: false, matched: null }))
    await api.lookup('don\'t & 天', 'en')

    expect(calls[0].url).toBe(`/api/lookup?word=${encodeURIComponent("don't & 天")}&lang=en`)
  })

  it('язык поиска по умолчанию китайский', async () => {
    const calls = stub(json({ word: '天', found: false, approximate: false, matched: null }))
    await api.lookup('天')

    expect(calls[0].url).toContain('lang=zh')
  })

  it('пустые параметры списка слов не уезжают в адрес', async () => {
    const calls = stub(json({ items: [], total: 0 }))
    await api.listWords()

    expect(calls[0].url).toBe('/api/words')
  })

  it('заданные параметры списка слов уезжают', async () => {
    const calls = stub(json({ items: [], total: 0 }))
    await api.listWords({ query: 'дом', limit: 10, offset: 20 })

    expect(calls[0].url).toContain('query=%D0%B4%D0%BE%D0%BC')
    expect(calls[0].url).toContain('limit=10')
    expect(calls[0].url).toContain('offset=20')
  })
})

// --- книги ---

describe('книги', () => {
  it('переименование уезжает PATCH-ом', async () => {
    const calls = stub(json({ id: 3, key: 'k', title: 'Книга' }))
    await api.renameBook(3, 'Книга')

    expect(calls[0].url).toBe('/api/books/3')
    expect(calls[0].init?.method).toBe('PATCH')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ title: 'Книга' })
  })

  it('пустое название уезжает, а не отбрасывается', async () => {
    // Оно значит «вернуть показ по адресу», и это осмысленное действие.
    const calls = stub(json({ id: 3, key: 'k', title: null }))
    await api.renameBook(3, '')

    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ title: '' })
  })

  it('удаление книги отвечает без тела', async () => {
    stub(new Response(null, { status: 204 }))
    await expect(api.deleteBook(3)).resolves.toBeUndefined()
  })

  it('выгрузка книги по умолчанию не переводит', async () => {
    // Полтора миллиона символов — половина месячного потолка; молча их
    // тратить нельзя, поэтому перевод просят явно.
    const calls = stub(json({ book_id: 3, running: true, loaded: 0, limit: 2000 }))
    await api.walkBook(3)

    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ translate: false })
  })

  it('перевод при выгрузке просится явно', async () => {
    const calls = stub(json({ book_id: 3, running: true, loaded: 0, limit: 2000 }))
    await api.walkBook(3, true)

    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ translate: true })
  })

  it('прогресс спрашивается тем же адресом, но GET-ом', async () => {
    const calls = stub(json({ book_id: 3, running: false, loaded: 7, limit: 2000 }))
    await api.bookWalk(3)

    expect(calls[0].url).toBe('/api/books/3/walk')
    expect(calls[0].init?.method).toBeUndefined()
  })

  it('остановка выгрузки — DELETE, и она отвечает состоянием', async () => {
    // Не 204: работа уходит не сразу, и ответ должен сказать, что уже сделано.
    const calls = stub(json({ book_id: 3, running: true, loaded: 40, cancelled: true }))
    const got = await api.stopBookWalk(3)

    expect(calls[0].url).toBe('/api/books/3/walk')
    expect(calls[0].init?.method).toBe('DELETE')
    expect(got.cancelled).toBe(true)
  })
})

// --- ссылка вперёд и окно браузера ---

describe('ссылка вперёд и окно браузера', () => {
  it('поиск ссылки заново — это POST без тела', async () => {
    const calls = stub(json({ id: 1, url: 'u', status: 'ready' }))
    await api.relinkChapter(1)

    expect(calls[0].url).toBe('/api/chapters/1/relink')
    expect(calls[0].init?.method).toBe('POST')
  })

  it('проверка сайта уезжает с адресом и ожиданием', async () => {
    const calls = stub(json({ ok: true, status: 200 }))
    await api.browserCheck('https://example.com/1', 30)

    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      url: 'https://example.com/1',
      seconds: 30,
    })
  })

  it('ожидание по умолчанию — минута', async () => {
    const calls = stub(json({ ok: true, status: 200 }))
    await api.browserCheck('https://example.com/1')

    expect(JSON.parse(String(calls[0].init?.body)).seconds).toBe(60)
  })

  it('снимок экрана — ссылка с меткой, а не запрос', async () => {
    // Файл один и всегда по одному адресу: без метки браузер показал бы
    // прошлую проверку вместо только что запущенной.
    const calls = stub(json({}))
    const first = browserScreenshotUrl(1)
    const second = browserScreenshotUrl(2)

    expect(first).not.toBe(second)
    expect(first.startsWith('/api/diagnostics/browser-check/screenshot?')).toBe(true)
    expect(calls).toHaveLength(0)
  })
})

// --- разбор отказов ---

describe('разбор отказов', () => {
  it('причина с бэкенда доезжает как есть', async () => {
    stub(json({ error: { kind: 'challenge', message: 'сайт просит проверку' } }, 403))

    await expect(api.getChapter(1)).rejects.toMatchObject({
      kind: 'challenge',
      message: 'сайт просит проверку',
    })
  })

  it('отказ — это всегда ApiError, а не голый Response', async () => {
    stub(json({ error: { kind: 'not_found', message: '' } }, 404))
    await expect(api.getChapter(1)).rejects.toBeInstanceOf(ApiError)
  })

  it('отказ без разбираемого тела не теряет статус', async () => {
    // Прокси и сам Caddy отвечают HTML, а не нашим форматом.
    stub(new Response('<html>502 Bad Gateway</html>', { status: 502 }))

    await expect(api.getChapter(1)).rejects.toMatchObject({ kind: 'http_502' })
  })

  it('обрыв сети опознаётся отдельно', async () => {
    // Сервер спит, телефон потерял вайфай — до ответа дело не дошло вовсе.
    stub(new TypeError('Failed to fetch'))

    await expect(api.getChapter(1)).rejects.toMatchObject({ kind: 'network' })
  })

  it('у ApiError всегда есть что показать', async () => {
    // Бэкенд вправе не прислать подробность. Пустое сообщение в этом случае
    // подменяется кодом причины: показать нечего — хуже, чем показать код.
    stub(json({ error: { kind: 'adapter_error', message: '' } }, 500))

    await expect(api.getChapter(1)).rejects.toMatchObject({ message: 'adapter_error' })
  })
})

// --- ответы без тела ---

describe('ответы без тела', () => {
  it('204 на удаление — это норма, а не сбой разбора', async () => {
    stub(new Response(null, { status: 204 }))
    await expect(api.deleteWord(1)).resolves.toBeUndefined()
  })
})

// --- озвучка ---

describe('audioUrl', () => {
  it('собирает адрес предложения', () => {
    expect(audioUrl(12, 0)).toBe('/api/chapters/12/audio/0')
  })

  it('это ссылка, а не запрос', async () => {
    // Её ставит <audio> и дальше сам решает, когда качать и как перематывать.
    const calls = stub(json({}))
    audioUrl(1, 2)
    expect(calls).toHaveLength(0)
  })
})

// --- статусы ---

describe('статусы главы', () => {
  it.each([
    ['fetching', false],
    ['segmented', true],
    ['translating', true],
    ['ready', true],
    ['failed', false],
  ] as [ChapterStatus, boolean][])('%s читаема: %s', (status, expected) => {
    // Глава читаема начиная с segmented: текст и токены уже есть, переводов
    // может не быть — и это состояние, а не отказ.
    expect(isReadable(status)).toBe(expected)
  })

  it.each([
    ['fetching', true],
    ['segmented', false],
    ['translating', true],
    ['ready', false],
    ['failed', false],
  ] as [ChapterStatus, boolean][])('%s требует опроса: %s', (status, expected) => {
    // Опрос обязан прекращаться сам: таймер не должен жить дольше работы.
    expect(isPending(status)).toBe(expected)
  })
})
