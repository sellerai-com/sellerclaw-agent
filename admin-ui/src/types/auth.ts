export interface ConnectRequest {
  email: string
  password: string
}

export interface AuthStatusResponse {
  connected: boolean
  user_id: string | null
  user_email: string | null
  user_name: string | null
  connected_at: string | null
}

export interface DisconnectResponse {
  status: string
}
