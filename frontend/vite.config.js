import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  publicDir: 'static',
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      '/data': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8765',
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
