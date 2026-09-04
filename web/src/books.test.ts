/**
 * Как книга называется на экране и что показывает её выгрузка.
 *
 * Имя из адреса — то, чем книга называется, пока её не назвали. Проверяется
 * здесь не красота, а то, что оно вообще получается: адрес приходит с чужого
 * сайта, и слаг в нём бывает каким угодно — с дефисами, с подчёркиваниями,
 * пустым, с хвостовым слэшем и без него. Пустая строка вместо имени превратила
 * бы список книг в список пробелов, по которому не выбрать.
 *
 * Строка о выгрузке — единственное, что читатель знает о часовой работе.
 * Поэтому состояний в ней пять, а не два: «оборвалась на отказе», «книга
 * кончилась» и «ссылки вперёд не было с самого начала» выглядят одинаково —
 * «загружено ноль», — а чинятся по-разному.
 */

import { describe, expect, it } from 'vitest'
import type { BookWalk } from './api'
import { bookLabel, bookName, describeWalk } from './books'

function walk(fields: Partial<BookWalk>): BookWalk {
  return {
    book_id: 1,
    running: false,
    loaded: 0,
    limit: 2000,
    stopped_by: null,
    cancelled: false,
    ...fields,
  }
}

describe('bookName', () => {
  it.each([
    ['https://novelarrow.com/novel/the-long-cartography/', 'the long cartography'],
    ['https://novelarrow.com/novel/the-long-cartography', 'the long cartography'],
    ['https://51shucheng.net/renwen/kniga/', 'kniga'],
    ['https://example.com/some_book_name/', 'some book name'],
  ])('%s → %s', (key, expected) => {
    expect(bookName(key)).toBe(expected)
  })

  it('терпит лишние слэши на конце', () => {
    expect(bookName('https://example.com/kniga///')).toBe('kniga')
  })

  it('никогда не отдаёт пустое имя', () => {
    // Иначе в списке книг окажется строка, по которой нечего нажать. Пустого
    // ключа среди входов нет: колонка `documents.key` не допускает NULL, а
    // заполняется адресом книги.
    for (const key of ['https://example.com/', 'https://example.com', '/']) {
      expect(bookName(key)).not.toBe('')
    }
  })

  it('у адреса без пути именем становится хост', () => {
    // Книга в корне сайта — случай странный, но показать её всё равно надо.
    expect(bookName('https://example.com/')).toBe('example.com')
  })
})

describe('bookLabel', () => {
  const key = 'https://novelarrow.com/novel/the-long-cartography/'

  it('название читателя важнее адреса', () => {
    expect(bookLabel({ key, title: 'Долгая картография' })).toBe('Долгая картография')
  })

  it('без названия показывается адрес', () => {
    expect(bookLabel({ key, title: null })).toBe('the long cartography')
  })

  it('название из пробелов равнозначно его отсутствию', () => {
    // Иначе в списке окажется строка из пробелов, по которой нечего нажать.
    expect(bookLabel({ key, title: '   ' })).toBe('the long cartography')
  })

  it('отсутствующее поле — тоже отсутствие названия', () => {
    expect(bookLabel({ key })).toBe('the long cartography')
  })
})

describe('describeWalk', () => {
  it('пока идёт — говорит, сколько уже загружено', () => {
    expect(describeWalk(walk({ running: true, loaded: 12 }), false)).toContain('12')
  })

  it('отказ называется по-человечески, а не кодом', () => {
    // `challenge` читателю ничего не говорит; «сайт просит пройти проверку» —
    // говорит, и по нему понятно, что делать дальше.
    const got = describeWalk(walk({ loaded: 3, stopped_by: 'challenge' }), false)

    expect(got).toContain('проверку')
    expect(got).not.toContain('challenge')
  })

  it('оборванная выгрузка не выдаёт себя за готовую', () => {
    const stopped = describeWalk(walk({ loaded: 3, stopped_by: 'challenge' }), false)
    const done = describeWalk(walk({ loaded: 3 }), false)

    expect(stopped).not.toBe(done)
    expect(done).toContain('книга кончилась')
  })

  it('конец книги без единой новой главы — не то же, что готовность', () => {
    // Проверять по «идти некуда» нельзя: эти слова есть и в строке о
    // готовности, и тест проходил бы, даже если ветку удалить целиком.
    const empty = describeWalk(walk({ loaded: 0, limit: 2000 }), false)
    const done = describeWalk(walk({ loaded: 5 }), false)

    expect(empty).toContain('последней странице')
    expect(empty).not.toBe(done)
  })

  it('до запуска предупреждает о расходе, если просили перевод', () => {
    expect(describeWalk(null, true)).toContain('лимит')
    expect(describeWalk(null, false)).not.toContain('лимит')
  })

  it('попросили остановиться — говорит, что уйдёт после этой главы', () => {
    // Между просьбой и остановкой проходит секунда-две: главу дописывают.
    const got = describeWalk(walk({ running: true, loaded: 40, cancelled: true }), false)

    expect(got).toContain('Остановлюсь')
  })

  it('остановка — не отказ и не конец книги', () => {
    const stopped = describeWalk(walk({ loaded: 40, cancelled: true }), false)

    expect(stopped).toContain('продолжить')
    expect(stopped).not.toContain('книга кончилась')
  })

  it('все исходы различимы между собой', () => {
    // Читатель судит о часовой работе по одной строке; совпавшие строки
    // означают, что два разных исхода он не различит вовсе.
    const texts = [
      describeWalk(walk({ running: true, loaded: 5 }), false),
      describeWalk(walk({ running: true, loaded: 5, cancelled: true }), false),
      describeWalk(walk({ loaded: 5, cancelled: true }), false),
      describeWalk(walk({ loaded: 5, stopped_by: 'challenge' }), false),
      describeWalk(walk({ loaded: 5 }), false),
      describeWalk(walk({ loaded: 0 }), false),
      describeWalk(null, false),
    ]

    expect(new Set(texts).size).toBe(texts.length)
  })
})
