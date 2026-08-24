/**
 * Разбор и сборка адреса.
 *
 * Роутер написан руками, поэтому проверять его приходится самим. Ломается он
 * в одном месте — на мусоре в хеше: адрес приезжает из истории браузера, из
 * закладки и из чужой ссылки, и `#/chapter/abc` не должен уносить экран в
 * ничто. Правило простое: всё, что не разобралось, — это экран ввода.
 *
 * `useRoute` здесь не проверяется: он подписан на `hashchange` в окне, а окна
 * в этих тестах нет намеренно.
 */

import { describe, expect, it } from 'vitest'
import { hrefFor, parseRoute, type Route } from './router'

describe('parseRoute', () => {
  it.each([
    ['#/chapter/12', { name: 'chapter', id: 12 }],
    ['#/books', { name: 'books' }],
    ['#/books/3', { name: 'book', id: 3 }],
    ['#/words', { name: 'words' }],
    ['#/diagnostics', { name: 'diagnostics' }],
    ['#/', { name: 'input' }],
    ['', { name: 'input' }],
  ] as [string, Route][])('%s → %o', (hash, expected) => {
    expect(parseRoute(hash)).toEqual(expected)
  })

  it.each(['#/books/abc', '#/books/0', '#/books/'])(
    'мусор в номере книги (%s) даёт список, а не пустоту',
    (hash) => {
      // Список — осмысленный ответ на «покажи книгу, которой нет»: с него
      // видно все остальные.
      expect(parseRoute(hash)).toEqual({ name: 'books' })
    },
  )

  it('терпит адрес без ведущего слэша', () => {
    expect(parseRoute('#chapter/7')).toEqual({ name: 'chapter', id: 7 })
  })

  it.each([
    '#/chapter/abc',
    '#/chapter/0',
    '#/chapter/-3',
    '#/chapter/1.5',
    '#/chapter/',
    '#/chapter',
    '#/неизвестно',
  ])('мусор %s ведёт на экран ввода, а не в пустоту', (hash) => {
    // Номер главы приезжает из истории и из чужих ссылок. Разбирать его
    // доверчиво значит однажды показать пустой экран вместо читалки.
    expect(parseRoute(hash)).toEqual({ name: 'input' })
  })

  it('лишний хвост маршрут не меняет', () => {
    expect(parseRoute('#/words/xxx')).toEqual({ name: 'words' })
  })
})

describe('hrefFor', () => {
  it.each([
    [{ name: 'chapter', id: 12 }, '#/chapter/12'],
    [{ name: 'books' }, '#/books'],
    [{ name: 'book', id: 3 }, '#/books/3'],
    [{ name: 'words' }, '#/words'],
    [{ name: 'diagnostics' }, '#/diagnostics'],
    [{ name: 'input' }, '#/'],
  ] as [Route, string][])('%o → %s', (route, expected) => {
    expect(hrefFor(route)).toBe(expected)
  })
})

describe('разбор и сборка', () => {
  it.each([
    { name: 'chapter', id: 1 },
    { name: 'chapter', id: 9999 },
    { name: 'books' },
    { name: 'book', id: 3 },
    { name: 'words' },
    { name: 'diagnostics' },
    { name: 'input' },
  ] as Route[])('%o переживает круг', (route) => {
    // Переход по ссылке и обратный разбор адреса обязаны сойтись: на этом
    // держится и кнопка «следующая глава», и перезагрузка страницы.
    expect(parseRoute(hrefFor(route))).toEqual(route)
  })
})
