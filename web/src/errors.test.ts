/**
 * Человеческие сообщения по `error_kind`.
 *
 * Проверяется не текст — он ещё будет меняться, — а два свойства, от которых
 * зависит поведение экрана.
 *
 * **Каждая причина различима.** «Челлендж» и «404» лечатся по-разному: первое
 * ожиданием, второе правкой адреса. Одинаковый текст на оба был бы хуже, чем
 * никакого: он выглядит как ответ, но им не является.
 *
 * **Флаг `readable` не врёт.** По нему экран решает, показывать ли текст главы
 * рядом с предупреждением. Ошибись в нём — и при отказе переводчика читатель
 * увидит пустоту вместо главы, которая лежит в базе целиком.
 */

import { describe, expect, it } from 'vitest'
import type { BrowserCheck, ErrorKind } from './api'
import { describeBrowserCheck, describeError, describeStatus } from './errors'

const KINDS: ErrorKind[] = [
  'challenge',
  'not_found',
  'empty_extract',
  'fetch_timeout',
  'adapter_error',
  'translate_failed',
  'speech_failed',
  'interrupted',
  'budget_exceeded',
  'bad_request',
]

describe('describeError', () => {
  it.each(KINDS)('%s объяснён и говорит, что делать', (kind) => {
    const info = describeError(kind)
    expect(info.title).not.toBe('')
    expect(info.advice).not.toBe('')
  })

  it('все причины различимы между собой', () => {
    const titles = KINDS.map((kind) => describeError(kind).title)
    expect(new Set(titles).size).toBe(titles.length)
  })

  it('неизвестная причина не роняет экран', () => {
    // Бэкенд может завести новый код раньше, чем фронт о нём узнает.
    const info = describeError('что-то-новое')
    expect(info.title).not.toBe('')
    expect(info.advice).not.toBe('')
  })

  it.each(['translate_failed', 'speech_failed', 'budget_exceeded'] as ErrorKind[])(
    '%s не прячет текст главы',
    (kind) => {
      // Текст и разметка уже в базе: отказ перевода или синтеза главу не отменяет.
      expect(describeError(kind).readable).toBe(true)
    },
  )

  it.each(['challenge', 'not_found', 'empty_extract'] as ErrorKind[])(
    '%s означает, что показывать нечего',
    (kind) => {
      expect(describeError(kind).readable).toBe(false)
    },
  )

  it('повтор предлагается там, где он осмыслен', () => {
    // Проверка сайта проходит сама, а свой потолок расходов повтором не лечится.
    expect(describeError('challenge').retryable).toBe(true)
    expect(describeError('budget_exceeded').retryable).toBe(false)
    expect(describeError('empty_extract').retryable).toBe(false)
  })

  it('прерванная перезапуском загрузка чинится ровно повтором', () => {
    // Ни сайт, ни провайдер тут ни при чём — глава просто попала под выкладку.
    expect(describeError('interrupted').retryable).toBe(true)
  })
})

describe('describeBrowserCheck', () => {
  function check(fields: Partial<BrowserCheck>): BrowserCheck {
    return {
      ok: false,
      kind: 'challenge',
      status: 403,
      title: '请稍候…',
      url: 'https://51shucheng.net/renwen/kniga/1.html',
      waited_seconds: 12,
      visible: true,
      screenshot: true,
      ...fields,
    }
  }

  it('удачная проверка говорит, что теперь заработает', () => {
    // Проверка не самоцель: её проходят, чтобы пошла загрузка глав.
    const got = describeBrowserCheck(check({ ok: true, kind: null, status: 200, title: '第1章' }))

    expect(got).toContain('открылась')
    expect(got).toContain('профиль')
  })

  it('невидимое окно — это про настройку, а не про капчу', () => {
    // Руками в headless-окне ничего не пройти, и советовать это жестоко.
    const got = describeBrowserCheck(check({ visible: false }))

    expect(got).toContain('BROWSER_HEADLESS')
  })

  it('видимое окно с челленджем зовёт смотреть на снимок', () => {
    const got = describeBrowserCheck(check({ visible: true }))

    expect(got).toContain('капча')
    expect(got).not.toContain('BROWSER_HEADLESS')
  })

  it('причина названа по-человечески, а не кодом', () => {
    const got = describeBrowserCheck(check({ kind: 'challenge' }))

    expect(got).not.toContain('challenge')
  })

  it('отказ без известной причины не теряет код ответа', () => {
    const got = describeBrowserCheck(check({ kind: null, status: 502 }))

    expect(got).toContain('502')
  })

  it('три исхода различимы между собой', () => {
    const texts = [
      describeBrowserCheck(check({ ok: true, kind: null })),
      describeBrowserCheck(check({ visible: false })),
      describeBrowserCheck(check({ visible: true })),
    ]

    expect(new Set(texts).size).toBe(3)
  })
})

describe('describeStatus', () => {
  it.each(['fetching', 'segmented', 'translating', 'ready'])('%s назван словами', (status) => {
    // Пользователь не должен читать перевод машинного кода.
    expect(describeStatus(status)).not.toBe(status)
  })

  it('незнакомый статус показывается как есть, а не пропадает', () => {
    expect(describeStatus('failed')).toBe('failed')
  })
})
