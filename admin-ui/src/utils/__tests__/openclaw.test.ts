import { describe, expect, it } from 'vitest'
import { formatUptime, statusLabel } from '../openclaw'

describe('formatUptime', () => {
  it.each([
    [0, '0s'],
    [45, '45s'],
    [90, '1m 30s'],
    [3600, '1h'],
    [3661, '1h 1m 1s'],
    [7200, '2h'],
    [-10, '0s'],
    [45.9, '45s'],
  ] as const)('formatUptime(%s) === %s', (seconds, expected) => {
    expect(formatUptime(seconds)).toBe(expected)
  })
})

describe('statusLabel', () => {
  it.each([
    ['running', 'Running'],
    ['stopped', 'Not running'],
    ['starting', 'Starting…'],
    ['error', 'Error'],
    ['custom_state', 'custom_state'],
  ] as const)('%s -> %s', (raw, expected) => {
    expect(statusLabel(raw)).toBe(expected)
  })
})
