import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import { OK_SESSION } from './fixture'
import type { RunRecord } from './delegate'

const R = '/h/.local/share/delegate/runs'
const NOW = Date.parse('2026-11-04T16:23:41Z')
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

// A finished run the worker wrote result.md for: its status and first summary line there.
const finished = (task: string, started: string, summary: string, resultStatus = 'done'): RunRecord =>
  rec(task, started, {
    attempts: [{ model: 'deepseek-v4.1-flash', outcome: 'ok', seconds: 10, session: null, ...usage }],
  })

const resultMd = (status: string, summary: string) => `## Status\n${status}\n\n## Summary\n${summary}\n`

const LIVE = '2026-11-04T11:20:00-0500'
const OLDER = '2026-11-04T11:30:00-0500'
const NEWER = '2026-11-04T11:50:00-0500'

let records: Record<string, RunRecord> = {}
let extraFiles: Record<string, string> = {}

const files = (): Record<string, string> => ({
  '/h/.local/share/delegate/runs.jsonl': Object.values(records).map(r => JSON.stringify(r)).join('\n') + '\n',
  // A live run's pi.log shows its model.
  ...Object.fromEntries(
    Object.values(records)
      .filter(r => r.status === 'running')
      .map(r => [`${r.run}/pi.log`, '=== deepseek-v4.1-flash 2026-11-04T11:40:00\n']),
  ),
  ...extraFiles,
})

const dirs = () => {
  const out: Record<string, string[]> = {}
  for (const r of Object.values(records)) if (r.status === 'running') out[`${r.run}/sessions`] = ['1.jsonl']
  return out
}

const seed = (on: On) => {
  // The engine's drawing where the band passes: the test's own ui.render stands for it.
  on('ui.render', () => Fragment({ children: [] }))
  on('ui.status', (_, e) => ({ value: undefined }))
  on('env.get', () => ({ value: '/h' }))
  on('clock.now', () => ({ value: NOW }))
  const FILES = files()
  const DIRS = dirs()
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

const BAND = (props: Record<string, unknown> = {}) =>
  ({ plugin: 'delegate-panel', component: 'AbovePrompt', requestId: 'band', surface: 'terminal', props } as const)

const BAND_PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 100,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}

const PANE = {
  plugin: 'delegate-panel',
  component: 'Pane',
  requestId: 'delegate',
  surface: 'terminal',
  props: {
    title: 'Delegate',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const spin = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[Math.floor(NOW / 1000) % 10]

test('the band passes when no run is pending', async ($, on) => {
  records = { 'clean-y': rec('clean-y', OLDER, { outcome: 'merged' }) }
  seed(on)
  const ui = await $.ui.mount(BAND(BAND_PROPS))
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /delegate/ })).toBeUndefined()
  await ui.unmount()
})

test('the band passes while a survey holds it', async ($, on) => {
  records = { 'live-a': rec('live-a', LIVE, { status: 'running' }) }
  extraFiles = { [`${R}/live-a/running`]: '4242\n', [`${R}/live-a/sessions/1.jsonl`]: PARTIAL }
  seed(on)
  const ui = await $.ui.mount(BAND({ ...BAND_PROPS, hasSurvey: true }))
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  await ui.unmount()
})

test('live first, then finished and failed newest first, with their icons', async ($, on) => {
  records = {
    'ok-older': finished('ok-older', OLDER, 'Added hello.txt and bye.txt.'),
    'live-a': rec('live-a', LIVE, { status: 'running' }),
    'ok-bad': finished('ok-bad', NEWER, 'Ran out of time.', 'failed'),
  }
  extraFiles = {
    [`${R}/live-a/running`]: '4242\n',
    [`${R}/live-a/sessions/1.jsonl`]: PARTIAL,
    [`${R}/ok-older/result.md`]: resultMd('done', 'Added hello.txt and bye.txt.'),
    [`${R}/ok-bad/result.md`]: resultMd('failed', 'Ran out of time.'),
  }
  seed(on)
  const ui = await $.ui.mount(BAND(BAND_PROPS))
  const flat = (text: string) => text.replace(/ +/g, ' ')
  expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual([
    'band-live-a',
    'band-ok-bad',
    'band-ok-older',
  ])

  // Icons: a spin over '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' for the live one, ✓ for review, ✗ for failed.
  expect(flat((await ui.find({ key: 'band-live-a' }))?.text ?? '')).toContain(`${spin} live-a`)
  expect(flat((await ui.find({ key: 'band-live-a' }))?.text ?? '')).toMatch(/running bash: pwd/)
  expect(flat((await ui.find({ key: 'band-ok-bad' }))?.text ?? '')).toContain('✗ ok-bad failed')
  expect(flat((await ui.find({ key: 'band-ok-older' }))?.text ?? '')).toContain('✓ ok-older review ds-flash 10s')
  expect(await ui.find({ type: 'Text', text: 'Added hello.txt and bye.txt.' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Ran out of time.' })).toBeDefined()
  // The task reads plain; model and elapsed are dim.
  expect((await ui.find({ type: 'Text', text: /^ok-older\s*$/ }))?.props.dimColor).toBe(false)
  expect((await ui.find({ type: 'Text', text: /^ds-flash\s*$/ }))?.props.dimColor).toBe(true)
  await ui.unmount()
})

test('seven runs leave five rows and a more line', async ($, on) => {
  records = {
    'live-a': rec('live-a', LIVE, { status: 'running' }),
    ...Object.fromEntries(
      Array.from({ length: 5 }, (_, i) => {
        const task = `many-${i}`
        return [task, finished(task, `2026-11-04T11:${String(30 + i).padStart(2, '0')}:00-0500`, `${task} wrapped up.`)]
      }),
    ),
    'live-b': rec('live-b', '2026-11-04T11:05:00-0500', { status: 'running' }),
  } as Record<string, RunRecord>
  extraFiles = {
    [`${R}/live-a/running`]: '4242\n',
    [`${R}/live-a/sessions/1.jsonl`]: PARTIAL,
    [`${R}/live-b/running`]: '4242\n',
    [`${R}/live-b/sessions/1.jsonl`]: PARTIAL,
  }
  seed(on)
  const ui = await $.ui.mount(BAND(BAND_PROPS))
  expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual([
    'band-live-b',
    'band-live-a',
    'band-many-4',
    'band-many-3',
    'band-many-2',
  ])
  expect(await ui.find({ type: 'Text', text: '+2 more · /delegate' })).toBeDefined()
  await ui.unmount()
})

test('pressing a row sets the view to that task and opens the pane', async ($, on) => {
  records = {
    'live-a': rec('live-a', LIVE, { status: 'running' }),
    'ok-z': finished('ok-z', NEWER, 'Added the report.'),
  }
  extraFiles = {
    [`${R}/live-a/running`]: '4242\n',
    [`${R}/live-a/sessions/1.jsonl`]: PARTIAL,
    [`${R}/ok-z/result.md`]: resultMd('done', 'Added the report.'),
  }
  // Nothing open yet, so a press must open the pane; what a press asks for is heard here.
  const opened: string[] = []
  on('ui.panes', () => ({ value: [] }))
  on('ui.open', (_, e) => (opened.push(e.id), { value: { id: e.id, title: e.title, isPlaced: true, focus: false, reason: '' } }))
  seed(on)
  const ui = await $.ui.mount(BAND(BAND_PROPS))

  expect(opened).toEqual([])
  await ui.press({ key: 'band-live-a' })
  expect(opened).toEqual(['delegate'])

  // The view atom stands at the pressed run: the pane draws it.
  const pane = await $.ui.mount(PANE)
  expect(await pane.find({ type: 'Text', text: 'live-a' })).toBeDefined()
  await pane.unmount()
  await ui.unmount()
})
