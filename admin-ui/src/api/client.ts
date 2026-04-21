import axios from 'axios'

const baseURL = import.meta.env.VITE_AGENT_BASE_URL ?? ''

export const apiClient = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
})

let bootstrapDone = false
let bootstrapInFlight: Promise<void> | null = null

function localBootstrapUrl(): string {
  const b = baseURL.replace(/\/$/, '')
  return b ? `${b}/auth/local-bootstrap` : '/auth/local-bootstrap'
}

/** Fetch loopback-only local API key and attach Bearer to all control-plane requests. */
export async function initApiClient(): Promise<void> {
  if (bootstrapDone) {
    return
  }
  if (!bootstrapInFlight) {
    bootstrapInFlight = (async () => {
      const { data } = await axios.get<{ local_api_key: string }>(localBootstrapUrl())
      const key = data?.local_api_key
      if (typeof key !== 'string' || !key.trim()) {
        throw new Error('local_bootstrap_invalid')
      }
      apiClient.defaults.headers.common.Authorization = `Bearer ${key.trim()}`
      bootstrapDone = true
    })()
  }
  try {
    await bootstrapInFlight
  } finally {
    bootstrapInFlight = null
  }
}
