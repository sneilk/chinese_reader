/**
 * Каркас приложения: верхняя панель и три маршрута (RFC §9).
 *
 * Экраны наполняются дальше: ввод URL — T1.14, чтение — T1.15 и T1.16,
 * словарь — T2.8.
 */

import { hrefFor, useRoute, type Route } from './router'
import { DiagnosticsScreen } from './screens/DiagnosticsScreen'
import { InputScreen } from './screens/InputScreen'
import { ReaderScreen } from './screens/ReaderScreen'
import { WordsScreen } from './screens/WordsScreen'

function NavLink({
  route,
  active,
  children,
}: {
  route: Route
  active: boolean
  children: React.ReactNode
}) {
  return (
    <a
      className={`topbar__link${active ? ' topbar__link--active' : ''}`}
      href={hrefFor(route)}
      aria-current={active ? 'page' : undefined}
    >
      {children}
    </a>
  )
}

export default function App() {
  const route = useRoute()
  const reading = route.name === 'chapter'

  return (
    <div className="layout">
      <header className="topbar">
        <a className="topbar__brand" href={hrefFor({ name: 'input' })}>
          chinese_reader
        </a>
        <NavLink route={{ name: 'input' }} active={route.name === 'input'}>
          Глава
        </NavLink>
        <NavLink route={{ name: 'words' }} active={route.name === 'words'}>
          Словарь
        </NavLink>
        <NavLink route={{ name: 'diagnostics' }} active={route.name === 'diagnostics'}>
          Состояние
        </NavLink>
      </header>

      <main className={`content${reading ? ' content--reader' : ''}`}>
        {route.name === 'input' && <InputScreen />}
        {route.name === 'chapter' && <ReaderScreen id={route.id} />}
        {route.name === 'words' && <WordsScreen />}
        {route.name === 'diagnostics' && <DiagnosticsScreen />}
      </main>
    </div>
  )
}
