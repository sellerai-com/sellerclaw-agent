import axios from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('initApiClient', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('fetches /auth/local-bootstrap and sets Bearer on apiClient', async () => {
    vi.spyOn(axios, 'get').mockResolvedValue({ data: { local_api_key: 'local-secret' } })
    const { initApiClient, apiClient } = await import('../client')
    await initApiClient()
    expect(axios.get).toHaveBeenCalledWith(expect.stringMatching(/\/auth\/local-bootstrap$/))
    expect(apiClient.defaults.headers.common.Authorization).toBe('Bearer local-secret')
  })

  it('second initApiClient is a no-op', async () => {
    vi.spyOn(axios, 'get').mockResolvedValue({ data: { local_api_key: 'k' } })
    const { initApiClient } = await import('../client')
    await initApiClient()
    await initApiClient()
    expect(axios.get).toHaveBeenCalledTimes(1)
  })

  it('concurrent initApiClient shares one bootstrap request', async () => {
    vi.spyOn(axios, 'get').mockResolvedValue({ data: { local_api_key: 'shared' } })
    const { initApiClient, apiClient } = await import('../client')
    await Promise.all([initApiClient(), initApiClient()])
    expect(axios.get).toHaveBeenCalledTimes(1)
    expect(apiClient.defaults.headers.common.Authorization).toBe('Bearer shared')
  })

  it('throws when bootstrap payload is invalid', async () => {
    vi.spyOn(axios, 'get').mockResolvedValue({ data: { local_api_key: '' } })
    const { initApiClient } = await import('../client')
    await expect(initApiClient()).rejects.toThrow('local_bootstrap_invalid')
  })

  it('interceptor awaits bootstrap before outbound request (no 401 race)', async () => {
    const order: string[] = []
    let resolveBootstrap: (v: { data: { local_api_key: string } }) => void = () => {}
    const bootstrapPromise = new Promise<{ data: { local_api_key: string } }>((resolve) => {
      resolveBootstrap = resolve
    })
    vi.spyOn(axios, 'get').mockImplementation(async (url: string) => {
      if (url.endsWith('/auth/local-bootstrap')) {
        order.push('bootstrap_called')
        const v = await bootstrapPromise
        order.push('bootstrap_resolved')
        return v
      }
      throw new Error(`unexpected axios.get ${url}`)
    })

    const { apiClient } = await import('../client')
    apiClient.defaults.adapter = async (config) => {
      order.push('adapter_invoked')
      return {
        data: { ok: true },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      } as never
    }

    const requestPromise = apiClient.get('/openclaw/status')
    // Let the interceptor register bootstrap first, then resolve.
    await Promise.resolve()
    resolveBootstrap({ data: { local_api_key: 'late-key' } })
    await requestPromise

    expect(order).toEqual(['bootstrap_called', 'bootstrap_resolved', 'adapter_invoked'])
    expect(apiClient.defaults.headers.common.Authorization).toBe('Bearer late-key')
  })
})
