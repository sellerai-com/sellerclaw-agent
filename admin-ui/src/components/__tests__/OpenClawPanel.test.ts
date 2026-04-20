import { mount, flushPromises } from '@vue/test-utils'
import axios, { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OpenClawPanel from '../OpenClawPanel.vue'

const {
  getOpenClawStatus,
  startOpenClaw,
  stopOpenClaw,
  restartOpenClaw,
} = vi.hoisted(() => ({
  getOpenClawStatus: vi.fn(),
  startOpenClaw: vi.fn(),
  stopOpenClaw: vi.fn(),
  restartOpenClaw: vi.fn(),
}))

vi.mock('../../api/openclaw', () => ({
  getOpenClawStatus,
  startOpenClaw,
  stopOpenClaw,
  restartOpenClaw,
}))

function stoppedStatus() {
  return {
    status: 'stopped',
    container_name: 'sellerclaw-openclaw',
    container_id: null,
    image: null,
    uptime_seconds: null,
    ports: { gateway: 7788, vnc: 6080 },
    error: null,
  }
}

function runningStatus() {
  return {
    status: 'running',
    container_name: 'sellerclaw-openclaw',
    container_id: '1234',
    image: 'openclaw:test',
    uptime_seconds: 65,
    ports: { gateway: 7788, vnc: 6080 },
    error: null,
  }
}

function startingStatus() {
  return {
    status: 'starting',
    container_name: 'sellerclaw-openclaw',
    container_id: null,
    image: null,
    uptime_seconds: null,
    ports: { gateway: 7788, vnc: 6080 },
    error: null,
  }
}

function findButton(wrapper: ReturnType<typeof mount>, label: string) {
  const buttons = wrapper.findAll('button')
  const found = buttons.find((b) => b.text().trim() === label)
  expect(found).toBeDefined()
  return found!
}

function expectBtnDisabled(btn: { element: Element }, value: boolean) {
  expect((btn.element as HTMLButtonElement).disabled).toBe(value)
}

describe('OpenClawPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    getOpenClawStatus.mockResolvedValue(stoppedStatus())
    startOpenClaw.mockResolvedValue({ outcome: 'completed', error: null })
    stopOpenClaw.mockResolvedValue({ outcome: 'completed', error: null })
    restartOpenClaw.mockResolvedValue({ outcome: 'completed', error: null })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('loads status on mount and shows label', async () => {
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expect(getOpenClawStatus).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Not running')
    expect(wrapper.text()).not.toContain('stopped')
    wrapper.unmount()
  })

  it('running: Start disabled, Stop and Restart enabled, shows uptime', async () => {
    getOpenClawStatus.mockResolvedValue(runningStatus())
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Running')
    expect(wrapper.text()).toContain('1m 5s')
    expectBtnDisabled(findButton(wrapper, 'Start'), true)
    expectBtnDisabled(findButton(wrapper, 'Stop'), false)
    expectBtnDisabled(findButton(wrapper, 'Restart'), false)
    wrapper.unmount()
  })

  it('stopped: Start enabled, Stop and Restart disabled', async () => {
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expectBtnDisabled(findButton(wrapper, 'Start'), false)
    expectBtnDisabled(findButton(wrapper, 'Stop'), true)
    expectBtnDisabled(findButton(wrapper, 'Restart'), true)
    wrapper.unmount()
  })

  it('starting: Start disabled, Stop and Restart disabled, shows label', async () => {
    getOpenClawStatus.mockResolvedValue(startingStatus())
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Starting…')
    expectBtnDisabled(findButton(wrapper, 'Start'), true)
    expectBtnDisabled(findButton(wrapper, 'Stop'), true)
    expectBtnDisabled(findButton(wrapper, 'Restart'), true)
    wrapper.unmount()
  })

  it('shows error when getOpenClawStatus throws', async () => {
    getOpenClawStatus.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('network down')
    wrapper.unmount()
  })

  it('Start completed shows success message', async () => {
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Start').trigger('click')
    await flushPromises()
    expect(startOpenClaw).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('OpenClaw start completed.')
    wrapper.unmount()
  })

  it('Start failed outcome shows error', async () => {
    startOpenClaw.mockResolvedValueOnce({ outcome: 'failed', error: 'supervisor error' })
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Start').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('supervisor error')
    wrapper.unmount()
  })

  it('Start HTTP 409 shows detail.message', async () => {
    const err = new AxiosError('Conflict')
    err.response = {
      status: 409,
      data: { detail: { code: 'x', message: 'already running hint' } },
    } as AxiosError['response']
    startOpenClaw.mockRejectedValueOnce(err)
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Start').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('already running hint')
    expect(axios.isAxiosError(err)).toBe(true)
    wrapper.unmount()
  })

  it('Stop completed shows success message', async () => {
    getOpenClawStatus.mockResolvedValue(runningStatus())
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Stop').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('OpenClaw stopped.')
    wrapper.unmount()
  })

  it('Stop failed outcome shows error', async () => {
    getOpenClawStatus.mockResolvedValue(runningStatus())
    stopOpenClaw.mockResolvedValueOnce({ outcome: 'failed', error: 'timeout' })
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Stop').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('timeout')
    wrapper.unmount()
  })

  it('Restart completed shows success message', async () => {
    getOpenClawStatus.mockResolvedValue(runningStatus())
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    await findButton(wrapper, 'Restart').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('OpenClaw restarted.')
    wrapper.unmount()
  })

  it('does not poll getOpenClawStatus while busy', async () => {
    let resolveStart!: (v: { outcome: string; error: null }) => void
    startOpenClaw.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        }),
    )
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    expect(getOpenClawStatus).toHaveBeenCalledTimes(1)
    const start = findButton(wrapper, 'Start')
    void start.trigger('click')
    await flushPromises()
    expectBtnDisabled(start, true)
    vi.advanceTimersByTime(8000)
    await flushPromises()
    expect(getOpenClawStatus).toHaveBeenCalledTimes(1)
    resolveStart!({ outcome: 'completed', error: null })
    await flushPromises()
    expect(getOpenClawStatus.mock.calls.length).toBeGreaterThanOrEqual(2)
    wrapper.unmount()
  })

  it('clears poll interval on unmount', async () => {
    const clearSpy = vi.spyOn(globalThis, 'clearInterval')
    const wrapper = mount(OpenClawPanel)
    await flushPromises()
    wrapper.unmount()
    expect(clearSpy).toHaveBeenCalled()
    clearSpy.mockRestore()
  })
})
