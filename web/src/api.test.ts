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
import { ApiError, api, audioUrl, isPending, isReadable, type ChapterStatus } from './api'

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
