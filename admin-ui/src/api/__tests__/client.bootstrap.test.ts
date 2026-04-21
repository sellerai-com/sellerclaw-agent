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
})
