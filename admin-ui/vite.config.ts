import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/admin/',
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5174,
    strictPort: true,
    host: true,
  },
})
