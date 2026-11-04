import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import { fold, parseSession, pending, segmentOf, type RunRecord, type Segment } from './delegate'
import { ERROR_SESSION, OK_SESSION } from './fixture'

const R = '/h/.local/share/delegate/runs'
const NOW = Date.parse('2026-11-04T17:00:00Z')
const usage = { input: 1, output: 2, cache_read: 3, cache_write: 0, cost: 0.001 }

const rec = (task: string, started: string, rest: Partial<RunRecord>): RunRecord => ({
  task,
  run: `${R}/${task}`,
  started,
  status: 'ok',
  outcome: null,
  attempts: [],
  ...rest,
})

const RECORDS = [
  rec('done-a', '2026-11-04T11:23:11-0500', {
    attempts: [
      { model: 'does-not-exist', outcome: 'api_error', seconds: 15, session: `${R}/done-a/sessions/1.jsonl`, ...usage },
      { model: 'deepseek', outcome: 'ok', seconds: 20, session: `${R}/done-a/sessions/2.jsonl`, ...usage },
      { model: 'deepseek', outcome: 'ok', seconds: 13, session: `${R}/done-a/sessions/2.jsonl`, retry: true, ...usage },
    ],
  }),
  rec('clean-c', '2026-11-04T11:50:00-0500', { outcome: 'merged' }),
  rec('live-b', '2026-11-04T11:40:00-0500', {
    status: 'running',
    attempts: [{ model: 'm1', outcome: 'timeout', seconds: 60, session: `${R}/live-b/sessions/1.jsonl`, ...usage }],
  }),
]

const FILES: Record<string, string> = {
  '/h/.local/share/delegate/runs.jsonl': RECORDS.map(r => JSON.stringify(r)).join('\n') + '\n',
  [`${R}/done-a/result.md`]: '## Status\ndone\n\n## Summary\nAdded hello.txt and bye.txt.\n',
  [`${R}/done-a/sessions/1.jsonl`]: ERROR_SESSION,
  [`${R}/done-a/sessions/2.jsonl`]: OK_SESSION,
  [`${R}/live-b/running`]: '4242\n',
  [`${R}/live-b/pi.log`]: '=== m1 2026-11-04T11:40:00\n=== m2 2026-11-04T11:41:00\n',
  [`${R}/live-b/brief.md`]: '# Goal\nlive brief\n',
  [`${R}/live-b/sessions/1.jsonl`]: OK_SESSION,
  [`${R}/live-b/sessions/2.jsonl`]: ERROR_SESSION,
}
const DIRS: Record<string, string[]> = { [`${R}/live-b/sessions`]: ['1.jsonl', '2.jsonl'] }

const PANE = {
  plugin: 'delegate-panel',
  component: 'Pane',
  requestId: 'delegate',
  props: {
    title: 'Delegate',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const seed = (on: On) => {
  on('env.get', () => ({ value: '/h' }))
  on('clock.now', () => ({ value: NOW }))
  on('fs.read', (_, e) => (e.path in FILES ? { value: FILES[e.path]! } : { deny: 'ENOENT' }))
  on('fs.stat', (_, e) =>
    e.path in FILES
      ? { value: { kind: 'file', size: FILES[e.path]!.length, mtimeMs: NOW - 90_000, isLink: false } }
      : { deny: 'ENOENT' },
  )
  on('fs.exists', (_, e) => ({ value: e.path === '/proc/4242' || e.path in FILES }))
  on('fs.list', (_, e) => ({
    value: (DIRS[e.path] ?? []).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })),
  }))
}

test('pending keeps uncleaned runs, newest first', () => {
  expect(pending(RECORDS).map(r => r.task)).toEqual(['live-b', 'done-a'])
})

test('a pi session parses into transcript segments', () => {
  const [first, retry] = parseSession(OK_SESSION) as [Segment, Segment]
  expect(first.items[0]).toEqual({ kind: 'user', text: expect.stringContaining('# Goal') })
  expect(first.items[1]).toMatchObject({
    kind: 'tool',
    name: 'bash',
    args: 'pwd && ls -la && git status --short && git log --oneline -3',
    output: expect.stringContaining('c1da6e4 smoke: init'),
    isError: false,
  })
  expect(first.items.at(-1)).toMatchObject({ kind: 'text', text: expect.stringContaining('Done.') })
  expect(first.usage.output).toBeGreaterThan(0)
  expect(retry.items[0]).toMatchObject({ kind: 'user', text: expect.stringContaining('bye.txt') })
  expect(retry.items[1]).toMatchObject({ kind: 'tool', name: 'write', args: 'bye.txt' })
  expect(parseSession(ERROR_SESSION)[0]?.items[1]).toMatchObject({
    kind: 'error',
    text: expect.stringContaining('Model is unavailable'),
  })
  // A retry's attempt takes the second segment of the shared file.
  expect(segmentOf([first, retry], ['a', 'b', 'b'], 2)).toBe(retry)
})

test('output past six lines folds', () => {
  const text = 'a\nb\nc\nd\ne\nf\ng\nh\n'
  expect(fold(text, false)).toEqual({ shown: 'a\nb\nc\nd\ne\nf', hidden: 2 })
  expect(fold(text, true)).toEqual({ shown: 'a\nb\nc\nd\ne\nf\ng\nh', hidden: 0 })
  expect(fold('a\nb', false).hidden).toBe(0)
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`the list, a session, its attempts and folding (${surface})`, async ($, on) => {
    seed(on)
    const ui = await $.ui.mount({ ...PANE, surface })

    const rows = await ui.findAll({ type: 'Button' })
    expect(rows.map(b => b.key)).toEqual(['open-live-b', 'open-done-a'])
    expect((await ui.find({ key: 'open-live-b' }))?.text).toMatch(/live-b fell back m2 · 1m30s/)
    expect((await ui.find({ key: 'open-done-a' }))?.text).toMatch(/done-a done deepseek · 48s/)
    expect(await ui.find({ type: 'Text', text: 'Added hello.txt and bye.txt.' })).toBeDefined()

    // Opens on the latest attempt: the retry's segment of the shared log.
    await ui.press({ key: 'open-done-a' })
    expect(await ui.find({ type: 'Text', text: 'feedback' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /attempt 3\/3/ })).toBeDefined()

    await ui.press({ key: 'attempt-0' })
    expect(await ui.find({ type: 'Text', text: /Model is unavailable/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /attempt 1\/3 \(api_error\)/ })).toBeDefined()

    await ui.press({ key: 'attempt-1' })
    expect(await ui.find({ type: 'Text', text: 'brief' })).toBeDefined()
    const fold1 = (await ui.findAll({ type: 'Button' })).find(b => b.key?.startsWith('fold-'))
    expect(fold1?.text).toBe('… 1 more lines')
    expect(await ui.find({ type: 'Text', text: /c1da6e4 smoke: init/ })).toBeUndefined()
    await ui.press({ key: fold1!.key! })
    expect(await ui.find({ type: 'Text', text: /c1da6e4 smoke: init/ })).toBeDefined()
    expect((await ui.find({ key: fold1!.key! }))?.text).toBe('fold')

    // The live run's current attempt reads the newest log no attempt owns yet.
    await ui.press({ key: 'back' })
    await ui.press({ key: 'open-live-b' })
    expect(await ui.find({ type: 'Text', text: /m2 · attempt 2\/2 \(running\)/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Model is unavailable/ })).toBeDefined()
    await ui.unmount()
  })
}
