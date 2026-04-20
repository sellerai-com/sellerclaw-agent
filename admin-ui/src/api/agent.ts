import axios from 'axios'
import type {
  GetManifestResponse,
  SaveManifestRequest,
  SaveManifestResponse,
} from '../types/manifest'
import { apiClient } from './client'

export async function getManifest(): Promise<GetManifestResponse | null> {
  try {
    const { data } = await apiClient.get<GetManifestResponse>('/manifest')
    return data
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      return null
    }
    throw err
  }
}

export async function saveManifest(
  body: SaveManifestRequest,
): Promise<SaveManifestResponse> {
  const { data } = await apiClient.post<SaveManifestResponse>('/manifest', body)
  return data
}

export function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const msg = (detail as { message?: unknown }).message
      if (typeof msg === 'string' && msg.length > 0) {
        return msg
      }
    }
    if (detail) return JSON.stringify(detail)
    return err.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}
