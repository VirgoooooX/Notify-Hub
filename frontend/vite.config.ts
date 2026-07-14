import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
export default defineConfig({plugins:[vue()],resolve:{alias:{'@':fileURLToPath(new URL('./src',import.meta.url))}},server:{proxy:{'/api':'http://localhost:8000','/health':'http://localhost:8000'}},test:{environment:'jsdom',setupFiles:['./tests/setup.ts'],css:true}})
