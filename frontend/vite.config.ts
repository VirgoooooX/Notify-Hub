import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

const allowedHosts = (process.env.NOTIFY_HUB_DEV_ALLOWED_HOSTS ?? '')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    allowedHosts,
    proxy: { '/api': 'http://localhost:8000', '/health': 'http://localhost:8000' },
  },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], css: true },
})
