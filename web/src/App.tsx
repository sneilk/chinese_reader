import { useEffect, useState } from 'react'

/**
 * Каркас (T1.1). Настоящие экраны — ввод URL, чтение, словарь — появятся
 * в T1.13–T1.16. Пока задача одна: показать, что фронт достаёт backend.
 */
export default function App() {
  const [status, setStatus] = useState('проверяю…')

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setStatus(`backend: ${d.status}`))
      .catch((e: Error) => setStatus(`backend недоступен: ${e.message}`))
  }, [])

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: 40, maxWidth: 720 }}>
      <h1>chinese_reader</h1>
      <p style={{ color: '#666' }}>{status}</p>
      <p style={{ fontSize: 28, lineHeight: 1.8 }}>我喜欢学习中文</p>
    </main>
  )
}
