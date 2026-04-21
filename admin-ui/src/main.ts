import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import { initApiClient } from './api/client'

const el = document.getElementById('app')
if (!el) {
  throw new Error('Missing #app root element')
}

initApiClient()
  .then(() => {
    createApp(App).mount(el)
  })
  .catch(() => {
    el.innerHTML =
      '<p style="padding:1rem;font-family:system-ui">Admin UI недоступен: запустите агент на localhost и откройте консоль с того же хоста (нужен /auth/local-bootstrap).</p>'
  })
