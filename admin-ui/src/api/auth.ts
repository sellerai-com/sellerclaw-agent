import type { AuthStatusResponse, ConnectRequest, DisconnectResponse } from '../types/auth'
import { apiClient } from './client'

export async function getAuthStatus(): Promise<AuthStatusResponse> {
  const { data } = await apiClient.get<AuthStatusResponse>('/auth/status')
  return data
}

export async function connectSellerClaw(body: ConnectRequest): Promise<AuthStatusResponse> {
  const { data } = await apiClient.post<AuthStatusResponse>('/auth/connect', body)
  return data
}

export async function disconnectSellerClaw(): Promise<DisconnectResponse> {
  const { data } = await apiClient.post<DisconnectResponse>('/auth/disconnect')
  return data
}
