import { apiClient } from './client'
import type { OpenClawCommandResponse, OpenClawStatusResponse } from '../types/openclaw'

export async function getOpenClawStatus(): Promise<OpenClawStatusResponse> {
  const { data } = await apiClient.get<OpenClawStatusResponse>('/openclaw/status')
  return data
}

export async function startOpenClaw(): Promise<OpenClawCommandResponse> {
  const { data } = await apiClient.post<OpenClawCommandResponse>('/openclaw/start')
  return data
}

export async function stopOpenClaw(): Promise<OpenClawCommandResponse> {
  const { data } = await apiClient.post<OpenClawCommandResponse>('/openclaw/stop')
  return data
}

export async function restartOpenClaw(): Promise<OpenClawCommandResponse> {
  const { data } = await apiClient.post<OpenClawCommandResponse>('/openclaw/restart')
  return data
}
