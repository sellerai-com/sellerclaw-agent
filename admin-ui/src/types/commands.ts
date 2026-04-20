export interface CommandHistoryEntry {
  command_id: string
  command_type: string
  issued_at: string
  received_at: string
  executed_at: string | null
  outcome: string | null
  error: string | null
}

export interface CommandHistoryResponse {
  entries: CommandHistoryEntry[]
}
