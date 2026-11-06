import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import {
  MODELS,
  activity,
  bandOrder,
  fold,
  look,
  parseSession,
  pending,
  plain,
  rowLine,
  segmentOf,
  shortModel,
  sum,
  widths,
  type RunRecord,
  type Segment,
} from './delegate'
import { ERROR_SESSION, OK_SESSION } from './fixture'

const R = '/h/.local/share/delegate/runs'
const NOW = Date.parse('2026-11-04T16:23:41Z') // 10s after OK_SESSION's first tool calls
// OK_SESSION cut after the first tool calls: a worker running bash, as a live log reads mid-run.
const PARTIAL = OK_SESSION.split('\n').slice(0, 5).join('\n') + '\n'
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
  [`${R}/live-b/sessions/2.jsonl`]: PARTIAL,
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

let statuses: string[] = []

const seed = (on: On) => {
  statuses = []
  on('ui.status', (_, e) => (statuses.push(e.text ?? ''), { value: undefined }))
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

test('escape sequences in a log are dropped: a pane refuses control characters', () => {
  const colored = OK_SESSION.replace('c1da6e4 smoke: init', '\\u001b[1;32mc1da6e4\\u001b[0m smoke: init\\r')
  const tool = parseSession(colored)[0]!.items[1] as { output: string }
  expect(tool.output).toContain('c1da6e4 smoke: init\n')
  expect(plain('a\x1b]0;title\x07b\tc\x00')).toBe('ab\tc')
})

test('output past six lines folds', () => {
  const text = 'a\nb\nc\nd\ne\nf\ng\nh\n'
  expect(fold(text, false)).toEqual({ shown: 'a\nb\nc\nd\ne\nf', hidden: 2 })
  expect(fold(text, true)).toEqual({ shown: 'a\nb\nc\nd\ne\nf\ng\nh', hidden: 0 })
  expect(fold('a\nb', false).hidden).toBe(0)
})

test('short model names shrink the ones the panel knows', () => {
  expect(shortModel('deepseek-v4.1-flash')).toBe('ds-flash')
  expect(shortModel('kimi-k2.7-code')).toBe('kimi-code')
  expect(shortModel('glm-5.3-flash')).toBe('glm-flash')
  expect(shortModel('claude-haiku-5-5')).toBe('haiku')
  expect(shortModel('mimo-v2.6-flash')).toBe('mimo-flash')
  expect(shortModel('some-other-model')).toBe('some-other-model')
  expect(Object.keys(MODELS)).toHaveLength(5)
})

test('a run looks live, stalled, fell back or finished', () => {
  expect(look('running', true, false, '⠋')).toEqual({ icon: '⠋', color: 'suggestion', word: 'running' })
  expect(look('fell back', true, false, '⠙')).toEqual({ icon: '⠙', color: 'warning', word: 'fell back' })
  expect(look('fell back', true, true, '!')).toEqual({ icon: '⚠', color: 'warning', word: 'stalled' })
  expect(look('done', false, false, '')).toEqual({ icon: '✓', color: 'success', word: 'review' })
  expect(look('ok', false, false, '')).toEqual({ icon: '✓', color: 'success', word: 'review' })
  for (const state of ['timed out', 'failed', 'api error', 'killed', 'blocked', 'stopped'])
    expect(look(state, false, false, '')).toEqual({ icon: '✗', color: 'error', word: state })
  expect(look('needs review', false, false, '')).toEqual({ icon: '✓', color: 'success', word: 'needs review' })
  expect(look('merge blocked', false, false, '')).toEqual({ icon: '✗', color: 'error', word: 'merge blocked' })
  expect(look('unexpected error', false, false, '')).toEqual({ icon: '✗', color: 'error', word: 'unexpected error' })
})

test('the band orders live runs oldest first, then the rest newest first', () => {
  const rows = [
    { live: false, rec: rec('done-old', '2026-11-04T11:00:00-0500', {}) },
    { live: true, rec: rec('live-new', '2026-11-04T11:40:00-0500', {}) },
    { live: false, rec: rec('done-new', '2026-11-04T12:00:00-0500', {}) },
    { live: true, rec: rec('live-old', '2026-11-04T11:20:00-0500', {}) },
  ]
  expect(bandOrder(rows).map(r => r.rec.task)).toEqual(['live-old', 'live-new', 'done-new', 'done-old'])
  expect(rows.map(r => r.rec.task)).toEqual(['done-old', 'live-new', 'done-new', 'live-old'])
})

test('a row line pads shared columns and cuts to the pane width', () => {
  const fields = [
    { icon: '✓', task: 'a', word: 'review', model: 'ds-flash', elapsed: '1m02s', detail: 'first' },
    { icon: '✗', task: 'a-much-longer-task-name', word: 'timed out', model: 'kimi-code', elapsed: '4s', detail: 'second' },
  ]
  const w = widths(fields)
  expect(w).toEqual({ task: 23, word: 9, model: 9, elapsed: 5 })
  expect(rowLine(fields[0]!, 120, w)).toEqual({
    icon: '✓',
    task: 'a'.padEnd(23),
    word: 'review'.padEnd(9),
    model: 'ds-flash'.padEnd(9),
    elapsed: '1m02s',
    detail: 'first',
  })
  expect(widths([{ ...fields[0]!, task: 'x'.repeat(40) }]).task).toBe(24)

  const cut = rowLine(
    { icon: '⠋', task: 'long-task-name', word: 'running', model: 'glm-flash', elapsed: '2m10s', detail: 'a detail far too long to fit' },
    30,
    widths([{ task: 'long-task-name', word: 'running', model: 'glm-flash', elapsed: '2m10s' }]),
  )
  const joined = [cut.icon, cut.task, cut.word, cut.model, cut.elapsed, cut.detail].filter(Boolean).join(' ')
  expect(joined).toBe('⠋ long-task-name running glm-…')
  expect(joined.length).toBe(30)

  // Never wider than the pane, even at its narrowest.
  for (const columns of [1, 5, 10, 20]) {
    const narrow = rowLine(fields[1]!, columns, w)
    const text = [narrow.icon, narrow.task, narrow.word, narrow.model, narrow.elapsed, narrow.detail].filter(Boolean).join(' ')
    expect(text.length).toBeLessThanOrEqual(columns)
    expect(text.endsWith('…')).toBe(true)
  }
})

test('activity: the running tool, waiting on the model, done, stalled, fell back', () => {
  const lines = OK_SESSION.split('\n')
  const seg = (n: number) => parseSession(lines.slice(0, n).join('\n'))[0]!
  expect(activity(true, 'running', 'm', seg(5), 0, NOW).text).toBe(
    'running bash: pwd && ls -la && git status --short && git log --oneline -3',
  )
  expect(activity(true, 'running', 'm', seg(5), 0, NOW)).toMatchObject({ idle: 9998, stalled: false })
  expect(activity(true, 'running', 'm', seg(6), 0, NOW).text).toBe('running bash: echo "$DELEGATE_RUN"')
  expect(activity(true, 'running', 'm', seg(7), 0, NOW).text).toBe('waiting on the model')
  expect(activity(true, 'running', 'm', seg(8), 0, NOW).text).toBe('done')
  expect(activity(true, 'running', 'm', undefined, NOW - 5000, NOW)).toMatchObject({ text: 'starting pi', idle: 5000 })
  const stalled = activity(true, 'fell back', 'm2', seg(7), 0, NOW + 120_000)
  expect(stalled).toMatchObject({ text: 'fell back to m2 · waiting on the model · stalled', stalled: true, spin: '!' })
  expect(activity(false, 'timed out', 'm', seg(7), 0, NOW + 120_000)).toMatchObject({ text: 'timed out', stalled: false })
  // Tool calls carry their elapsed time from the log's timestamps.
  expect(seg(6).items[1]).toMatchObject({ started: Date.parse('2026-11-04T16:23:31.002Z'), ended: Date.parse('2026-11-04T16:23:31.023Z') })
})

test('live token sums grow with a partial session', () => {
  const partial = parseSession(PARTIAL)[0]!.usage
  expect(partial).toMatchObject({ input: 1977, output: 134, cache_read: 256, cache_write: 0 })
  const full = parseSession(OK_SESSION)[0]!.usage
  expect(full).toMatchObject({ input: 2297, output: 216, cache_read: 3072 })
  expect(sum([usage, partial]).input).toBe(1978)
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`the list, a session, its attempts and folding (${surface})`, async ($, on) => {
    seed(on)
    const ui = await $.ui.mount({ ...PANE, surface })

    const rows = await ui.findAll({ type: 'Button' })
    expect(rows.map(b => b.key)).toEqual(['open-live-b', 'open-done-a', 'close'])
    expect((await ui.find({ key: 'open-live-b' }))?.text).toMatch(/live-b \S fell back to m2 · running bash: pwd .* m2 · 1m30s/)
    expect((await ui.find({ key: 'open-done-a' }))?.text).toMatch(/done-a done deepseek · 48s/)
    expect(await ui.find({ type: 'Text', text: 'Added hello.txt and bye.txt.' })).toBeDefined()
    // Running tokens and cost per row: live-b's recorded attempt (6) plus its live log so far (2367).
    expect(await ui.find({ type: 'Text', text: /^2.4k tok \$0\.0014 · last activity 10s ago$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^18 tok \$0\.0030$/ })).toBeDefined()

    // Opens on the latest attempt: the retry's segment of the shared log.
    await ui.press({ key: 'open-done-a' })
    // The jump control sits at the top, right under the header, and opening scrolled to the end.
    const keys = (await ui.findAll({ type: 'Button' })).map(b => b.key)
    expect(keys.slice(0, 2)).toEqual(['back', 'end'])
    // The way back is repeated at the bottom, where following leaves the view.
    expect(keys.slice(-2)).toEqual(['back-end', 'close'])
    expect((await ui.find({ key: 'end' }))?.text).toBe('↓ jump to end (e)')
    // The kit has no scroller, so the plugin's scroll rejects; that it was asked shows on the status line.
    expect(statuses).toEqual(['delegate: jump to end failed: HooksError: no implementation for ui.scroll'])
    // The state and token line show at the top and again in the bottom status line.
    expect((await ui.findAll({ type: 'Text', text: /^following · attempt 6 tok/ })).length).toBe(2)
    // result.md in full at the end.
    expect(await ui.find({ type: 'Text', text: 'result.md' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /## Status\ndone\n\n## Summary\nAdded hello/ })).toBeDefined()
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
    await ui.press({ key: 'back-end' })
    await ui.press({ key: 'open-live-b' })
    expect(await ui.find({ type: 'Text', text: /m2 · attempt 2\/2 \(running\)/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /fell back to m2 · running bash: pwd/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /last activity 10s ago/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /… running 10s/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^attempt: in 2.0k · out 134 · cache read 256 · cache write 0 · total 2.4k/ })).toBeDefined()
    await ui.unmount()
  })
}
