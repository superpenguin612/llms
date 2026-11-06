import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { View } from '../types'
import {
  type AttemptRecord,
  type RunRecord,
  type Segment,
  type Usage,
  activity,
  parseRecords,
  parseSession,
  plain,
  resultInfo,
  segmentOf,
  stateOf,
  sum,
} from './delegate'

export const PANE = 'delegate'
export const TITLE = 'Delegate'
export const READ_CAP = 4 * 1024 * 1024 // $.fs.read rejects past 4 MiB

export const view = atom({ plugin: 'delegate-panel', key: 'view' } as const, {} as View, { shape: 'view-1' })
export const expanded = atom({ plugin: 'delegate-panel', key: 'expanded' } as const, [] as string[], { shape: 'expanded-1' })
// Whether the session view stays at its end: the person scrolling up stops it, back to the end resumes it.
export const following = atom({ plugin: 'delegate-panel', key: 'following' } as const, true, { shape: 'following-1' })

type $ = EngineInterface

// The state scanner follows a source only to the file that writes its atom, so register.tsx
// reads and writes the pane's state through these.
export function readView($: $) {
  return read($, view)
}
export function writeView($: $, change: (value: View) => View) {
  return update($, view, change)
}
export function readExpanded($: $) {
  return read($, expanded)
}
export function writeExpanded($: $, change: (list: string[]) => string[]) {
  return update($, expanded, change)
}
export function readFollowing($: $) {
  return read($, following)
}

export function readText($: $, path: string) {
  return $.fs.read(path).then(t => plain(t as string), () => undefined)
}

// shortcut: a log past the 4 MiB read cap shows its last ~4 MiB only (read through `tail`), so a
// retry's segment can be misnumbered and early entries are gone; upgrade if workers' logs grow that large.
export async function readLog($: $, path: string) {
  const size = await $.fs.stat(path).then(s => s.size, () => -1)
  if (size < 0) return ''
  if (size < READ_CAP) return (await readText($, path)) ?? ''
  const tail = await $.process.run(['tail', '-c', String(READ_CAP - 1024), path]).catch(() => undefined)
  return plain(tail?.stdout ?? '')
}

// Live as delegate itself decides it: a `running` marker whose PID is alive (Linux /proc).
export async function isLive($: $, run: string) {
  const pid = (await readText($, `${run}/running`))?.trim()
  return !!pid && $.fs.exists(`/proc/${pid}`)
}

// pi.log gets a `=== <model> <time>` line as each attempt starts.
export async function liveModel($: $, run: string) {
  return ((await readLog($, `${run}/pi.log`)).match(/^=== (\S+)/gm)?.at(-1) ?? '=== -').slice(4)
}

export async function loadRecords($: $) {
  const home = (await $.env.get('HOME')) ?? ''
  return parseRecords((await readText($, `${home}/.local/share/delegate/runs.jsonl`)) ?? '')
}

export type Attempt = { model: string; outcome: string; session?: string | null; usage?: Usage }

export type Row = {
  rec: RunRecord
  live: boolean
  model: string
  elapsed: number
  state: string
  result?: string
  resultMd?: string
  attempts: Attempt[]
  current?: Segment // the live attempt's session so far
  act: ReturnType<typeof activity>
  total: Usage // the whole run: recorded attempts plus the live one so far
}

// The recorded attempts, plus the one running now: its session is the newest log no attempt
// owns yet, or, for a retry (feedback.md written), the last attempt's log, which pi appends to.
export async function attemptsOf($: $, run: string, done: Attempt[], model: string): Promise<Attempt[]> {
  const owned = new Set(done.map(a => a.session))
  const logs = (await $.fs.list(`${run}/sessions`).catch(() => []))
    .map(f => `${run}/sessions/${f.name}`)
    .filter(p => p.endsWith('.jsonl') && !owned.has(p))
    .sort()
  const isRetry = await $.fs.exists(`${run}/feedback.md`)
  const session = logs.at(-1) ?? (isRetry ? done.at(-1)?.session : undefined)
  return [...done, { model, outcome: 'running', session }]
}

export async function rowOf($: $, rec: RunRecord, now: number): Promise<Row> {
  const live = await isLive($, rec.run)
  const resultMd = await readText($, `${rec.run}/result.md`)
  const info = resultInfo(resultMd ?? '')
  const since = live ? await $.fs.stat(`${rec.run}/running`).then(s => s.mtimeMs, () => now) : now
  const model = live ? await liveModel($, rec.run) : (rec.attempts.at(-1)?.model ?? '-')
  const done: Attempt[] = rec.attempts.map((a: AttemptRecord) => ({ ...a, usage: a }))
  const attempts = live ? await attemptsOf($, rec.run, done, model) : done
  const session = live ? attempts.at(-1)?.session : undefined
  const current = session
    ? segmentOf(parseSession(await readLog($, session)), attempts.map(a => a.session), attempts.length - 1)
    : undefined
  const state = stateOf(rec, live, info.status)
  return {
    rec,
    live,
    model,
    elapsed: live ? (now - since) / 1000 : rec.attempts.reduce((n, a) => n + a.seconds, 0),
    state,
    result: info.line,
    resultMd,
    attempts,
    current,
    act: activity(live, state, model, current, since, now),
    total: sum([...rec.attempts, ...(current ? [current.usage] : [])]),
  }
}

// `end` keeps up with the growing tree until anything next moves it. A refusal (`deny`: the
// window moved meanwhile, no surface) or a rejection (the test kit has no scroller: "no
// implementation") goes to the status line rather than nowhere.
export async function toEnd($: $) {
  const { deny } = await $.ui.scroll({ in: PANE, to: 'end' }).catch((err: unknown) => ({ deny: String(err) }))
  $.ui.status(deny ? `delegate: jump to end failed: ${deny}` : undefined)
}
export async function follow($: $) {
  await update($, following, () => true)
  await toEnd($)
}
