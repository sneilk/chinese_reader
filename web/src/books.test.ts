/**
 * Имя книги из её адреса.
 *
 * Единственное имя, которое у книги есть. Проверяется здесь не красота, а то,
 * что оно вообще получается: адрес приходит с чужого сайта, и слаг в нём бывает
 * каким угодно — с дефисами, с подчёркиваниями, пустым, с хвостовым слэшем и
 * без него. Пустая строка вместо имени превратила бы список книг в список
 * пробелов, по которому не выбрать.
 */

import { describe, expect, it } from 'vitest'
import { bookName } from './books'

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
