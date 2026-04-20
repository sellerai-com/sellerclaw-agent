import axios from 'axios'

const baseURL = import.meta.env.VITE_AGENT_BASE_URL ?? ''

export const apiClient = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
})
