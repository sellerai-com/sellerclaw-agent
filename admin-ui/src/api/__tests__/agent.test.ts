import axios, { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'
import { extractErrorMessage } from '../agent'

function makeAxiosError(
  message: string,
  response: { status: number; data?: { detail?: unknown } } | undefined,
): AxiosError {
  const err = new AxiosError(message)
  err.response = response as AxiosError['response']
  return err
}

describe('extractErrorMessage', () => {
  it('returns string detail as-is', () => {
    const err = makeAxiosError('fail', { status: 400, data: { detail: 'bad request' } })
    expect(extractErrorMessage(err)).toBe('bad request')
  })

  it('returns detail.message for structured FastAPI error', () => {
    const err = makeAxiosError('fail', {
      status: 409,
      data: { detail: { code: 'openclaw_already_running', message: 'Use restart instead.' } },
    })
    expect(extractErrorMessage(err)).toBe('Use restart instead.')
  })

  it('falls back to JSON.stringify when message is empty string', () => {
    const detail = { code: 'x', message: '' }
    const err = makeAxiosError('fail', { status: 409, data: { detail } })
    expect(extractErrorMessage(err)).toBe(JSON.stringify(detail))
  })

  it('stringifies array detail', () => {
    const detail = [{ loc: ['body'], msg: 'required', type: 'missing' }]
    const err = makeAxiosError('fail', { status: 422, data: { detail } })
    expect(extractErrorMessage(err)).toBe(JSON.stringify(detail))
  })

  it('uses err.message when no detail in response', () => {
    const err = makeAxiosError('Network Error', { status: 500, data: {} })
    expect(extractErrorMessage(err)).toBe('Network Error')
  })

  it('uses err.message when response is undefined', () => {
    const err = new AxiosError('timeout')
    expect(extractErrorMessage(err)).toBe('timeout')
  })

  it('returns Error message for plain Error', () => {
    expect(extractErrorMessage(new Error('oops'))).toBe('oops')
  })

  it('stringifies non-Error values', () => {
    expect(extractErrorMessage('plain')).toBe('plain')
    expect(extractErrorMessage(42)).toBe('42')
  })

  it('treats only axios errors with isAxiosError', () => {
    const fake = {
      message: 'x',
      response: { data: { detail: 'should not use' } },
    }
    expect(axios.isAxiosError(fake)).toBe(false)
    expect(extractErrorMessage(fake)).toBe(String(fake))
  })
})
