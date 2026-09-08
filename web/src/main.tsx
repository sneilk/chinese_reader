import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyTheme, loadTheme } from './theme'

// Атрибут темы проставляет inline-скрипт в index.html — до первой отрисовки,
// иначе видна вспышка чужой палитры. Здесь выбор применяется ещё раз, и это
// не дубль: скрипту в head нечем покрасить `theme-color`, потому что он не
// знает, во что разрешится «как в системе».
applyTheme(loadTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
