/** Human-readable status label for OpenClaw container state. */
export function statusLabel(s: string): string {
  if (s === 'running') return 'Running'
  if (s === 'stopped') return 'Not running'
  if (s === 'starting') return 'Starting…'
  if (s === 'error') return 'Error'
  return s
}

/** Format uptime seconds as e.g. "1h 5m 3s" or "45s". */
export function formatUptime(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const parts: string[] = []
  if (h > 0) parts.push(`${h}h`)
  if (m > 0) parts.push(`${m}m`)
  if (sec > 0 || parts.length === 0) parts.push(`${sec}s`)
  return parts.join(' ')
}
