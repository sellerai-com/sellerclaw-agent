export interface OpenClawStatusResponse {
  status: string
  container_name: string | null
  container_id: string | null
  image: string | null
  uptime_seconds: number | null
  ports: Record<string, number> | null
  error: string | null
}

export interface OpenClawCommandResponse {
  outcome: string
  error: string | null
}
