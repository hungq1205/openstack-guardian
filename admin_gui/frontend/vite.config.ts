import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Backend: `uv run python -m admin_gui.backend.main` (127.0.0.1:8765).
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
