/**
 * Выбор темы: разбор сохранённого значения, обход по кругу и то, во что
 * разрешается «как в системе».
 *
 * Проверяется чистая часть — та, что не трогает документ. Применение к DOM
 * проверяется руками в браузере: браузерного окружения в тестах нет намеренно
 * (см. README), а мок `matchMedia` проверял бы мок.
 *
 * Сторожит это то, что ломается молча. Разбор мусора из хранилища — самое
 * вероятное место: в localStorage может лежать что угодно, включая значение
 * от прошлой версии, и падать на нём приложению нельзя.
 */

import { describe, expect, it } from 'vitest'
import {
  THEME_COLOR,
  THEME_ORDER,
  describeTheme,
  nextTheme,
  parseTheme,
  resolveTheme,
  type ThemeChoice,
} from './theme'

describe('parseTheme', () => {
  it.each(['auto', 'dark', 'light'] as ThemeChoice[])('принимает %s', (value) => {
    expect(parseTheme(value)).toBe(value)
  })

  it.each([null, undefined, '', 'DARK', 'тёмная', 'system', '{"theme":"dark"}'])(
    'мусор %p читает как «как в системе»',
    (value) => {
      expect(parseTheme(value)).toBe('auto')
    },
  )
})

describe('nextTheme', () => {
  it('обходит все состояния по кругу и возвращается к началу', () => {
    const seen: ThemeChoice[] = []
    let current: ThemeChoice = 'auto'
    for (let i = 0; i < THEME_ORDER.length; i++) {
      seen.push(current)
      current = nextTheme(current)
    }

    expect(seen).toEqual(THEME_ORDER)
    expect(current).toBe('auto')
  })

  it('не застревает ни на одном значении', () => {
    for (const choice of THEME_ORDER) {
      expect(nextTheme(choice)).not.toBe(choice)
    }
  })
})

describe('resolveTheme', () => {
  it('явный выбор системы не спрашивает', () => {
    expect(resolveTheme('dark', true)).toBe('dark')
    expect(resolveTheme('light', false)).toBe('light')
  })

  it('«как в системе» идёт за системой', () => {
    expect(resolveTheme('auto', true)).toBe('light')
    expect(resolveTheme('auto', false)).toBe('dark')
  })

  it('без системного предпочтения остаётся тёмная — она основная', () => {
    expect(resolveTheme('auto', false)).toBe('dark')
  })
})

describe('describeTheme', () => {
  it('у каждого состояния своя подпись', () => {
    const labels = THEME_ORDER.map(describeTheme)
    expect(new Set(labels).size).toBe(THEME_ORDER.length)
    expect(labels.every((l) => l.length > 0)).toBe(true)
  })
})

describe('THEME_COLOR', () => {
  it('совпадает с --bg обеих тем: этим цветом красится строка состояния браузера', () => {
    // Значения продублированы из index.css намеренно: CSS отсюда не прочитать,
    // а разъехавшись, они дадут полосу чужого цвета над страницей.
    expect(THEME_COLOR.dark).toBe('#16181c')
    expect(THEME_COLOR.light).toBe('#fbfaf8')
  })
})
