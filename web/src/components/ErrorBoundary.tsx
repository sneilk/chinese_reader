/**
 * Последняя защита от белого экрана.
 *
 * Исключение в рендере React не показывает как ошибку — он размонтирует всё
 * дерево. Читатель видит пустую страницу, и единственный след случившегося
 * лежит в консоли, которую на телефоне не открыть.
 *
 * Граница не чинит поломку и не должна: она превращает «ничего» в «вот что
 * случилось и вот кнопка». Этого хватает, потому что чинится такое перезагрузкой
 * страницы, а вот **узнать**, что чинить, иначе неоткуда.
 *
 * Класс, а не хук: `componentDidCatch` в функциональных компонентах не
 * существует и не планируется — это единственное, ради чего в React ещё нужны
 * классы.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // В консоль — со стеком компонентов: он говорит, где именно рвануло,
    // а в тексте на экране ему не место.
    console.error('сбой в рендере:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (error === null) return this.props.children

    return (
      <div className="note note--error" role="alert">
        <div className="note__title">Экран не отрисовался</div>
        <p className="note__advice">
          Это поломка на нашей стороне, а не отказ сайта или переводчика. Данные целы:
          глава и словарь лежат в базе.
        </p>
        <p className="note__detail">
          <code>{error.message || String(error)}</code>
        </p>
        <button className="button button--quiet" type="button" onClick={() => location.reload()}>
          Перезагрузить
        </button>
      </div>
    )
  }
}
