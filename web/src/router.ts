/**
 * Мини-роутер на хеше. Три маршрута — библиотека тут была бы дороже кода.
 *
 * Хеш, а не History API, сознательно: при перезагрузке страницы на телефоне
 * `/chapter/12` требует, чтобы сервер отдавал index.html на любой путь, а это
 * лишнее требование к Caddy (T1.17), о котором легко забыть и обнаружить
 * поломку уже в бою. С хешем перезагрузка работает всегда и везде.
 */

import { useEffect, useState } from 'react'

export type Route =
  | { name: 'input' }
  | { name: 'chapter'; id: number }
  | { name: 'books' }
  | { name: 'book'; id: number }
  | { name: 'words' }
  | { name: 'diagnostics' }

/** Положительное целое или `null`: номер приезжает из истории и чужих ссылок. */
function identifier(raw: string | undefined): number | null {
  if (!raw) return null
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : null
}

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const [head, tail] = path.split('/')

  if (head === 'chapter') {
    const id = identifier(tail)
    if (id !== null) return { name: 'chapter', id }
  }
  if (head === 'books') {
    // `#/books` — список, `#/books/3` — оглавление одной книги. Мусор после
    // «books» разбирается как список, а не уносит экран в пустоту.
    const id = identifier(tail)
    return id !== null ? { name: 'book', id } : { name: 'books' }
  }
  if (head === 'words') return { name: 'words' }
  if (head === 'diagnostics') return { name: 'diagnostics' }
  return { name: 'input' }
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case 'chapter':
      return `#/chapter/${route.id}`
    case 'books':
      return '#/books'
    case 'book':
      return `#/books/${route.id}`
    case 'words':
      return '#/words'
    case 'diagnostics':
      return '#/diagnostics'
    default:
      return '#/'
  }
}

export function navigate(route: Route): void {
  window.location.hash = hrefFor(route)
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash))

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}
