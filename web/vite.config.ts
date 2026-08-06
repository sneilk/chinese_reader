import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// В разработке фронт и backend живут на разных портах, поэтому /api уходит
// через прокси. В бою оба за одним reverse proxy, и прокси не участвует.
export default defineConfig({
  plugins: [react()],
  server: {
    // Явный IPv4: по умолчанию Vite слушает localhost, который на macOS
    // резолвится в ::1, и обращения к 127.0.0.1 не проходят.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
