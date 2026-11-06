import type { FsEntry, On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const NOW = Date.parse('2026-11-04T16:00:00Z')
const HOME = '/h'
const DIR = `${HOME}/.local/state/claude-compactions`
const DAY = 24 * 60 * 60 * 1000
const HOUR = 60 * 60 * 1000
const PING = 50 * 60 * 1000
const KEEPALIVE = 'keepalive: reply "ok" only'

const hhmm = (ms: number) => {
  const at = new Date(ms)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

const seed = (on: On, entries: readonly FsEntry[] = []) => {
  const statuses: (string | undefined)[] = []
  const submitted: string[] = []
  const removed: string[] = []
  const commands: string[] = []
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  on('command.register', (_, e) => {
    commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', (_, e) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('fs.list', (_, e) => ({ value: e.path === DIR ? [...entries] : [] }))
  on('process.run', (_, e) => {
    removed.push(...e.argv)
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  return { clock, statuses, submitted, removed, commands }
}

const start = ($: Parameters<Parameters<typeof test>[1]>[0]) =>
  $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })

const turn = ($: Parameters<Parameters<typeof test>[1]>[0]) =>
  $.turn.complete({ answer: 'ok', durationMs: 1000, isAborted: false, turnId: 'turn-1', reason: 'answer' })

test('pruning removes compaction summaries older than 30 days', async ($, on) => {
  const old = { name: 'old.md', kind: 'file', size: 1, mtimeMs: NOW - 31 * DAY, isLink: false } as const
  const recent = { name: 'recent.md', kind: 'file', size: 1, mtimeMs: NOW - DAY, isLink: false } as const
  const { removed, commands } = seed(on, [old, recent])
  await start($)
  expect(commands).toContain('away')
  expect(removed).toContain(`${DIR}/old.md`)
  expect(removed).not.toContain(`${DIR}/recent.md`)
})

test('/away takes hours up to 12 and refuses the rest', async ($, on) => {
  const { statuses } = seed(on)
  await start($)

  for (const args of ['13', '0', 'abc']) {
    const run = await $.command.run({ command: 'away', args })
    expect(run.text).toMatch(/at most 12/)
  }
  expect(statuses).toEqual([])

  const two = await $.command.run({ command: 'away', args: '2' })
  const untilTwo = NOW + 2 * HOUR
  expect(two.text).toBe(`Away until ${hhmm(untilTwo)}: pinging every 50 min to keep the cache warm.`)
  expect(statuses.at(-1)).toBe(`away until ${hhmm(untilTwo)}`)

  const before = statuses.length
  for (const args of ['13', '0', 'abc']) {
    const run = await $.command.run({ command: 'away', args })
    expect(run.text).toMatch(/at most 12/)
  }
  expect(statuses.length).toBe(before)

  const oneAndAHalf = await $.command.run({ command: 'away', args: '1.5h' })
  const untilOneAndAHalf = NOW + 1.5 * HOUR
  expect(oneAndAHalf.text).toBe(`Away until ${hhmm(untilOneAndAHalf)}: pinging every 50 min to keep the cache warm.`)
  expect(statuses.at(-1)).toBe(`away until ${hhmm(untilOneAndAHalf)}`)
})

test('a turn re-arms the ping and it submits the keepalive prompt', async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '2' })

  await clock.advance(10 * 60 * 1000)
  await turn($)
  expect(submitted).toEqual([])

  await clock.advance(PING)
  expect(submitted.filter(text => text === KEEPALIVE)).toHaveLength(1)
  expect(statuses.at(-1)).toMatch(/^away until /)
})

test('an away shorter than the ping is cleared without a prompt', async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '0.5' })
  await turn($)

  await clock.advance(PING)
  expect(submitted).toEqual([])
  expect(statuses.at(-1)).toBeUndefined()
})

test("a person's prompt cancels the ping", async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '2' })

  await clock.advance(5 * 60 * 1000)
  await $.prompt.submit({ text: 'I am back' })
  expect(statuses.at(-1)).toBeUndefined()

  await clock.advance(PING)
  expect(submitted).toEqual(['I am back'])
})

test('/away off cancels', async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '2' })

  const off = await $.command.run({ command: 'away', args: 'off' })
  expect(off.text).toBe('Away cancelled.')
  expect(statuses.at(-1)).toBeUndefined()

  await clock.advance(PING)
  expect(submitted).toEqual([])
})
