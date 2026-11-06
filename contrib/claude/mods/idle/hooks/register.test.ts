import type { FsEntry, On, SessionCompactInput, SessionMessage } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const NOW = Date.parse('2026-11-04T16:00:00Z')
const HOME = '/h'
const CWD = '/w'
const DIR = `${HOME}/.local/state/claude-compactions`
const DAY = 24 * 60 * 60 * 1000
const HOUR = 60 * 60 * 1000
const PING = 50 * 60 * 1000
const KEEPALIVE = 'keepalive: reply "ok" only'
const PROMPT = 'Write a summary of this conversation so far for a fresh context that continues the work.'
const COMPACTED = 'Summarize in one sentence; this summary will be replaced.'
const HEADER =
  'This session is being continued from a previous conversation. The summary below covers the earlier portion of the conversation.\n\nSummary:\n'

const hhmm = (ms: number) => {
  const at = new Date(ms)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

const stamp = (ms: number) => {
  const at = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}${pad(at.getMonth() + 1)}${pad(at.getDate())}-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`
}

const path = `${DIR}/${stamp(NOW)}-w.md`

const message = (role: 'user' | 'assistant', text: string, rest: Partial<SessionMessage> = {}): SessionMessage => ({
  role,
  text,
  toolUses: [],
  ...rest,
})

const toolResult = (id: string) =>
  message('user', '', { toolResults: [{ tool_use_id: id, text: 'output', isError: false }] })

const transcript = [
  message('user', 'first request'),
  message('assistant', 'a1'),
  toolResult('t1'),
  message('assistant', 'a2'),
  message('user', 'second request'),
  message('assistant', 'a3'),
  toolResult('t2'),
  message('user', 'third request'),
  toolResult('t3'),
  message('assistant', 'a4'),
]

const uses = {
  input_tokens: 12_345,
  output_tokens: 2_000,
  cache_read_input_tokens: 45_600,
  cache_creation_input_tokens: 4_000,
}

const engine = (on: On, seen: SessionCompactInput[]) =>
  on('session.compact', (_, e) => {
    seen.push(e)
    return { messages: [message('user', 'ENGINE SUMMARY'), ...e.messages.slice(-2)] }
  })

const seed = (on: On, entries: readonly FsEntry[] = [], tokens = 0) => {
  const statuses: (string | undefined)[] = []
  const submitted: string[] = []
  const removed: string[] = []
  const commands: string[] = []
  const writes: { path: string; text: string }[] = []
  const logs: string[] = []
  const forks: string[] = []
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  on('command.register', (_, e) => {
    commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.usage', () => ({
    value: {
      startedAt: NOW,
      context: { tokens, window: 200_000, percent: 0 },
      rateLimits: [],
    },
  }))
  on('session.cwd', () => ({ value: CWD }))
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.log', (_, e) => {
    logs.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', (_, e) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('fs.list', (_, e) => ({ value: e.path === DIR ? [...entries] : [] }))
  on('fs.read', (_, e) =>
    e.path.endsWith('/hooks/summary-prompt.md')
      ? { value: `${PROMPT}\nmore prompt\n` }
      : { deny: 'ENOENT' },
  )
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    removed.push(...e.argv)
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  return { clock, statuses, submitted, removed, commands, writes, logs, forks }
}

const start = ($: Parameters<Parameters<typeof test>[1]>[0]) =>
  $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })

const turn = ($: Parameters<Parameters<typeof test>[1]>[0], id = 'turn-1') =>
  $.turn.complete({ answer: 'ok', durationMs: 1000, isAborted: false, turnId: id, reason: 'answer' })

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
  expect(two.text).toBe(
    `This session stays warm 2 h after each of your turns (now until ${hhmm(untilTwo)}).`,
  )
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(untilTwo)}`)

  const before = statuses.length
  for (const args of ['13', '0', 'abc']) {
    const run = await $.command.run({ command: 'away', args })
    expect(run.text).toMatch(/at most 12/)
  }
  expect(statuses.length).toBe(before)

  const oneAndAHalf = await $.command.run({ command: 'away', args: '1.5h' })
  const untilOneAndAHalf = NOW + 1.5 * HOUR
  expect(oneAndAHalf.text).toBe(
    `This session stays warm 1.5 h after each of your turns (now until ${hhmm(untilOneAndAHalf)}).`,
  )
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(untilOneAndAHalf)}`)
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
  expect(statuses.at(-1)).toMatch(/^warm until /)
})

test('a window shorter than the ping is cleared when its window ends', async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '0.5' })
  await turn($)

  await clock.advance(30 * 60 * 1000 - 1)
  expect(statuses.at(-1)).toMatch(/^warm until /)

  await clock.advance(1)
  expect(submitted).toEqual([])
  expect(statuses.at(-1)).toBeUndefined()
})

test("/away 5 keeps its window through a person's prompt and restarts it after the turn", async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '5' })

  await clock.advance(5 * 60 * 1000)
  await $.prompt.submit({ text: 'I am back' })
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(NOW + 5 * HOUR)}`)

  await turn($)
  const until = clock.now() + 5 * HOUR
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)
  expect(statuses.at(-1)).not.toBe(`warm until ${hhmm(clock.now() + 2 * HOUR)}`)

  await clock.advance(PING)
  expect(submitted.filter(text => text === KEEPALIVE)).toHaveLength(1)
})

test('/away typed after a turn pings 50 min after that turn, not after the command', async ($, on) => {
  const { clock, submitted } = seed(on)
  await start($)
  await turn($)

  await clock.advance(30 * 60 * 1000)
  await $.prompt.submit({ text: '/away 2' })
  await $.command.run({ command: 'away', args: '2' })

  await clock.advance(20 * 60 * 1000)
  expect(submitted.filter(text => text === KEEPALIVE)).toHaveLength(1)
})

test('/away off clears the window', async ($, on) => {
  const { clock, statuses, submitted } = seed(on)
  await start($)
  await $.command.run({ command: 'away', args: '2' })

  const off = await $.command.run({ command: 'away', args: 'off' })
  expect(off.text).toBe("This session won't be kept warm. /away <hours> turns it back on.")
  expect(statuses.at(-1)).toBeUndefined()

  await clock.advance(PING)
  expect(submitted).toEqual([])
})

test('a session over 50k tokens stays warm for 2 h and pings every 50 min', async ($, on) => {
  const { clock, statuses, submitted } = seed(on, [], 60_000)
  await start($)
  await turn($)

  const until = NOW + 2 * HOUR
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)

  await clock.advance(PING)
  expect(submitted).toEqual([KEEPALIVE])
  await turn($, 'ping-1')
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)

  await clock.advance(PING)
  expect(submitted).toEqual([KEEPALIVE, KEEPALIVE])
  await turn($, 'ping-2')

  await clock.advance(20 * 60 * 1000)
  expect(statuses.at(-1)).toBeUndefined()

  await clock.advance(PING)
  expect(submitted).toHaveLength(2)
})

test('a session under 50k tokens opens no window until /away 3', async ($, on) => {
  const { clock, statuses, submitted } = seed(on, [], 30_000)
  await start($)
  await turn($)

  expect(statuses.at(-1)).toBeUndefined()
  await clock.advance(3 * HOUR)
  expect(submitted).toEqual([])

  await $.command.run({ command: 'away', args: '3' })
  await turn($, 'turn-2')
  const until = clock.now() + 3 * HOUR
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)

  await clock.advance(PING)
  expect(submitted.filter(text => text === KEEPALIVE)).toHaveLength(1)
})

test('/away off opens nothing on the next turn; /away 2 turns it back on', async ($, on) => {
  const { clock, statuses, submitted } = seed(on, [], 60_000)
  await start($)

  await $.command.run({ command: 'away', args: 'off' })
  await turn($)
  expect(statuses.at(-1)).toBeUndefined()
  await clock.advance(PING)
  expect(submitted).toEqual([])

  const two = await $.command.run({ command: 'away', args: '2' })
  const untilTwo = clock.now() + 2 * HOUR
  expect(two.text).toBe(
    `This session stays warm 2 h after each of your turns (now until ${hhmm(untilTwo)}).`,
  )
  await turn($, 'turn-2')
  const until = clock.now() + 2 * HOUR
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)

  await clock.advance(PING)
  expect(submitted.filter(text => text === KEEPALIVE)).toHaveLength(1)
})

test('/away with no argument reports the current setting', async ($, on) => {
  const { clock, statuses } = seed(on, [], 60_000)
  await start($)
  await turn($)

  const until = NOW + 2 * HOUR
  const byDefault = await $.command.run({ command: 'away', args: '' })
  expect(byDefault.text).toBe(
    `Kept warm 2 h after each turn (default for sessions over 50k tokens); now until ${hhmm(until)}.\nUsage: /away <hours> (max 12), /away 1.5h, or /away off.`,
  )

  await $.command.run({ command: 'away', args: '5' })
  const untilFive = clock.now() + 5 * HOUR
  const explicit = await $.command.run({ command: 'away', args: '' })
  expect(explicit.text).toBe(
    `Kept warm 5 h after each turn (set with /away); now until ${hhmm(untilFive)}.\nUsage: /away <hours> (max 12), /away 1.5h, or /away off.`,
  )

  await $.command.run({ command: 'away', args: 'off' })
  const off = await $.command.run({ command: 'away', args: '' })
  expect(off.text).toBe(
    "Not kept warm (off). /away <hours> turns it back on.\nUsage: /away <hours> (max 12), /away 1.5h, or /away off.",
  )
  expect(statuses.at(-1)).toBeUndefined()
})

test('/away with no argument reports a session under 50k tokens', async ($, on) => {
  seed(on, [], 30_000)
  await start($)
  const run = await $.command.run({ command: 'away', args: '' })
  expect(run.text).toBe(
    "Not kept warm: the session is under 50k tokens. /away <hours> keeps it warm anyway.\nUsage: /away <hours> (max 12), /away 1.5h, or /away off.",
  )
})

test('a reload restores the warm window and pings from the last turn end', async ($, on) => {
  const { clock, statuses, submitted } = seed(on, [], 60_000)
  await start($)
  await turn($)

  const until = NOW + 2 * HOUR
  await clock.advance(10 * 60 * 1000)
  await start($)
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)

  await clock.advance(40 * 60 * 1000)
  expect(submitted).toEqual([KEEPALIVE])
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(until)}`)
})

test('engine compaction adds our prompt, saves the summary, and a prompt restores the warm status', async ($, on) => {
  const { statuses, writes } = seed(on, [], 60_000)
  const seen: SessionCompactInput[] = []
  engine(on, seen)
  await start($)
  await turn($)

  const result = await $.session.compact({
    trigger: 'manual',
    instructions: 'keep the API notes',
    messages: transcript,
  })

  expect(seen).toHaveLength(1)
  expect(seen[0]!.instructions).toMatch(/^The user asked to keep or stress: keep the API notes/)
  expect(seen[0]!.instructions).toContain(PROMPT)
  expect(result.messages[0]!.text).toBe('ENGINE SUMMARY')
  expect(writes).toEqual([{ path, text: 'ENGINE SUMMARY' }])
  expect(statuses.at(-1)).toBe(`compacted at ${hhmm(NOW)}`)

  await $.prompt.submit({ text: 'I am back' })
  expect(statuses.at(-1)).toBe(`warm until ${hhmm(NOW + 2 * HOUR)}`)
})

test('a subagent compaction is left untouched and saves nothing', async ($, on) => {
  const { statuses, writes } = seed(on)
  const seen: SessionCompactInput[] = []
  engine(on, seen)
  await start($)

  await $.session.compact({ trigger: 'manual', agentId: 'a1', messages: transcript })

  expect(seen).toHaveLength(1)
  expect(seen[0]!.agentId).toBe('a1')
  expect(seen[0]!.instructions).toBeUndefined()
  expect(writes).toEqual([])
  expect(statuses).toEqual([])
})

test(
  'mod compaction keeps the last two real prompts and saves the fork summary',
  { options: { compaction: 'mod' } },
  async ($, on) => {
    const { statuses, writes, logs } = seed(on)
    const seen: SessionCompactInput[] = []
    const prompts: string[] = []
    on('model.fork', (_, e) => {
      prompts.push(e.prompt)
      return { value: { isAnswered: true, text: '<analysis>walk through</analysis>\n<summary>\nS\n</summary>', usage: uses } }
    })
    engine(on, seen)
    await start($)

    const result = await $.session.compact({
      trigger: 'manual',
      instructions: 'keep the API notes',
      messages: transcript,
    })

    expect(transcript.slice(-2)[0]!.toolResults).toHaveLength(1)
    expect(seen).toHaveLength(1)
    expect(seen[0]!.messages).toEqual(transcript.slice(4))
    expect(seen[0]!.messages[0]!.text).toBe('second request')
    expect(seen[0]!.instructions).toBe(COMPACTED)
    expect(prompts[0]).toContain(PROMPT)
    expect(prompts[0]).toContain('The user asked to keep or stress: keep the API notes')
    expect(result.messages[0]).toEqual({ role: 'user', text: HEADER + 'S', toolUses: [] })
    expect(result.messages.slice(1)).toEqual(transcript.slice(4))
    expect(writes).toEqual([{ path, text: HEADER + 'S' }])
    expect(logs).toContain(
      'idle: summary from a fork of the session (cache read 45.6k, uncached 16.3k, output 2.0k)',
    )
    expect(statuses.at(-1)).toBe(`compacted at ${hhmm(NOW)}`)
  },
)

test(
  'mod compaction takes the engine path when the fork did not answer',
  { options: { compaction: 'mod' } },
  async ($, on) => {
    const { writes } = seed(on)
    const seen: SessionCompactInput[] = []
    on('model.fork', () => ({ value: { isAnswered: false, reason: 'nothing-to-fork' } }))
    engine(on, seen)
    await start($)

    const result = await $.session.compact({
      trigger: 'manual',
      instructions: 'keep the API notes',
      messages: transcript,
    })

    expect(seen).toHaveLength(1)
    expect(seen[0]!.instructions).toMatch(/^The user asked to keep or stress: keep the API notes/)
    expect(seen[0]!.instructions).toContain(PROMPT)
    expect(result.messages[0]!.text).toBe('ENGINE SUMMARY')
    expect(writes).toEqual([{ path, text: 'ENGINE SUMMARY' }])
  },
)

test(
  'mod compaction skips precompute',
  { options: { compaction: 'mod' } },
  async ($, on) => {
    const { statuses, writes } = seed(on)
    const seen: SessionCompactInput[] = []
    engine(on, seen)
    await start($)

    const result = await $.session.compact({ trigger: 'precompute', messages: transcript })

    expect(result).toEqual({ skip: 'idle mod writes the summary' })
    expect(seen).toEqual([])
    expect(writes).toEqual([])
    expect(statuses).toEqual([])
  },
)

test(
  'mod compaction falls back to the plain engine path when the engine refuses the tail',
  { options: { compaction: 'mod' } },
  async ($, on) => {
    const { writes } = seed(on)
    const seen: SessionCompactInput[] = []
    on('model.fork', () => ({ value: { isAnswered: true, text: 'S', usage: uses } }))
    on('session.compact', (_, e) => {
      seen.push(e)
      if (e.instructions === COMPACTED) return { skip: 'Not enough messages to compact.' }
      return { messages: [message('user', 'ENGINE SUMMARY'), ...e.messages.slice(-2)] }
    })
    await start($)

    const result = await $.session.compact({
      trigger: 'manual',
      instructions: 'keep the API notes',
      messages: transcript,
    })

    expect(seen).toHaveLength(2)
    expect(seen[0]!.messages).toEqual(transcript.slice(4))
    expect(seen[1]!.messages).toEqual(transcript)
    expect(seen[1]!.instructions).toMatch(/^The user asked to keep or stress: keep the API notes/)
    expect(result.messages[0]!.text).toBe('ENGINE SUMMARY')
    expect(writes).toEqual([{ path, text: 'ENGINE SUMMARY' }])
  },
)
