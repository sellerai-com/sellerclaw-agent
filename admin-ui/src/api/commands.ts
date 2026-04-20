import type { CommandHistoryEntry, CommandHistoryResponse } from '../types/commands'
import { apiClient } from './client'

export async function getCommandHistory(): Promise<CommandHistoryEntry[]> {
  const { data } = await apiClient.get<CommandHistoryResponse>('/commands/history')
  return data.entries
}
