// Pure parsing of delegate's run records and pi's session logs: no `$` here.

export type Usage = { input: number; output: number; cache_read: number; cache_write: number; cost: number }

export type AttemptRecord = Usage & { model: string; outcome: string; seconds: number; session: string | null; retry?: boolean }

export type RunRecord = {
  task: string
  run: string
  started: string
  status: string
  outcome: string | null
  attempts: AttemptRecord[]
}

// The short names a row shows for the models delegate runs.
export const MODELS: Record<string, string> = {
  'deepseek-v4.1-flash': 'ds-flash',
  'kimi-k2.7-code': 'kimi-code',
  'glm-5.3-flash': 'glm-flash',
  'claude-haiku-5-5': 'haiku',
  'mimo-v2.6-flash': 'mimo-flash',
}

export const shortModel = (name: string) => MODELS[name] ?? name

export type Item =
  | { kind: 'user'; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'error'; text: string }
  | { kind: 'tool'; id: string; name: string; args: string; output?: string; isError?: boolean; started: number; ended?: number }

// One pi prompt and everything after it: an attempt, or a retry appended to the same file.
// `last` is the newest entry's time (ms); `done` whether the model's last turn ended it.
export type Segment = { items: Item[]; usage: Usage; last: number; done: boolean }

export const FOLD = 6

const zero = (): Usage => ({ input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 })

export const sum = (us: Usage[]) =>
  us.reduce(
    (t, u) => ({
      input: t.input + u.input,
      output: t.output + u.output,
      cache_read: t.cache_read + u.cache_read,
      cache_write: t.cache_write + u.cache_write,
      cost: t.cost + u.cost,
    }),
    zero(),
  )

export const totalTokens = (u: Usage) => u.input + u.output + u.cache_read + u.cache_write

// A pane refuses text holding control characters: drop terminal escape sequences (colored tool
// output) and any other control character but newline and tab.
export const plain = (text: string) =>
  text
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]?/g, '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')

const parseLines = (text: string): unknown[] =>
  text.split('\n').flatMap(line => {
    try {
      return line.trim() ? [JSON.parse(line, (_, v) => (typeof v === 'string' ? plain(v) : v))] : []
    } catch {
      return [] // a line still being written, or cut by the tail read
    }
  })

export const parseRecords = (text: string): RunRecord[] => parseLines(text) as RunRecord[]

// delegate writes `2026-11-04T11:23:11-0500`; Date.parse wants `-05:00`.
export const startedMs = (started: string) => Date.parse(started.replace(/([+-]\d\d)(\d\d)$/, '$1:$2'))

// Runs not yet cleaned (live ones among them: clean refuses a live run), newest first.
export const pending = (records: RunRecord[]) =>
  records
    .filter(r => r.outcome === null)
    .sort((a, b) => startedMs(b.started) - startedMs(a.started))

// result.md's `## Status` value and the first line of its summary (else its first plain line).
export const resultInfo = (md: string) => {
  const sections: Record<string, string[]> = {}
  let at = ''
  const plain: string[] = []
  for (const raw of md.split('\n')) {
    const line = raw.trim()
    const heading = /^#+\s*(.*)$/.exec(line)
    if (heading) at = (heading[1] ?? '').toLowerCase()
    else if (line) {
      ;(sections[at] ??= []).push(line)
      plain.push(line)
    }
  }
  return { status: sections.status?.[0], line: sections.summary?.[0] ?? plain[0] }
}

const FAILED: Record<string, string> = { timeout: 'timed out', api_error: 'api error', failed: 'failed' }

export const stateOf = (rec: RunRecord, isLive: boolean, resultStatus?: string) => {
  if (isLive) return rec.attempts.some(a => a.outcome === 'timeout' || a.outcome === 'api_error') ? 'fell back' : 'running'
  if (rec.status === 'ok') return resultStatus ?? 'done'
  return FAILED[rec.status] ?? (rec.status === 'running' ? 'stopped' : rec.status)
}

const BAD = new Set(['timed out', 'failed', 'api error', 'killed', 'blocked', 'stopped'])

// A run's icon, color and the word its row prints, from its state word and its activity.
export const look = (state: string, live: boolean, stalled: boolean, spin: string) => {
  if (live && stalled) return { icon: '⚠', color: 'warning', word: 'stalled' }
  if (live) return { icon: spin, color: state === 'fell back' ? 'warning' : 'suggestion', word: state }
  if (state === 'done' || state === 'ok') return { icon: '✓', color: 'success', word: 'review' }
  if (BAD.has(state)) return { icon: '✗', color: 'error', word: state }
  const bad = /fail|block|error/i.test(state)
  return { icon: bad ? '✗' : '✓', color: bad ? 'error' : 'success', word: state }
}

// Live runs first, oldest first: they are what the band watches. Then the rest, newest first.
export const bandOrder = <T extends { live: boolean; rec: Pick<RunRecord, 'started'> }>(rows: T[]): T[] =>
  [...rows].sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1
    const at = startedMs(a.rec.started)
    const bt = startedMs(b.rec.started)
    return a.live ? at - bt : bt - at
  })

export type RowFields = { icon: string; task: string; word: string; model: string; elapsed: string; detail?: string }
export type RowWidths = { task: number; word: number; model: number; elapsed: number }

const longest = (values: string[]) => values.reduce((n, text) => Math.max(n, text.length), 0)

// The column widths one set of rows shares: each as wide as its longest value, the task at most 24.
export const widths = (rows: Pick<RowFields, 'task' | 'word' | 'model' | 'elapsed'>[]): RowWidths => ({
  task: Math.min(longest(rows.map(r => r.task)), 24),
  word: longest(rows.map(r => r.word)),
  model: longest(rows.map(r => r.model)),
  elapsed: longest(rows.map(r => r.elapsed)),
})

const pad = (text: string, width: number) =>
  text.length > width ? `${text.slice(0, Math.max(0, width - 1))}…` : text.padEnd(width)

const LINE = ['icon', 'task', 'word', 'model', 'elapsed', 'detail'] as const
const BLANK: Required<RowFields> = { icon: '', task: '', word: '', model: '', elapsed: '', detail: '' }

// `{icon} {task} {word} {model} {elapsed} {detail}` padded to the shared widths and cut to the
// pane's columns with a trailing ellipsis; the pieces stay apart so a caller colors the icon and word.
export const rowLine = (fields: RowFields, columns: number, w: RowWidths = widths([fields])): Required<RowFields> => {
  if (columns <= 0) return { ...BLANK }
  const parts: Required<RowFields> = {
    icon: fields.icon,
    task: pad(fields.task, w.task),
    word: pad(fields.word, w.word),
    model: pad(fields.model, w.model),
    elapsed: pad(fields.elapsed, w.elapsed),
    detail: fields.detail ?? '',
  }
  const starts: number[] = []
  let line = ''
  for (const key of LINE) {
    if (!parts[key]) continue
    if (line) line += ' '
    starts.push(line.length)
    line += parts[key]
  }
  if (line.length <= columns) return parts
  const used = LINE.filter(key => parts[key])
  const shown = line.slice(0, columns - 1)
  const cut: Required<RowFields> = { ...BLANK }
  used.forEach((key, i) => {
    const from = starts[i]!
    const to = i + 1 < starts.length ? starts[i + 1]! - 1 : shown.length
    cut[key] = shown.slice(from, Math.max(from, to))
  })
  const last = [...used].reverse().find(key => cut[key] !== '') ?? used[0]
  if (last) cut[last] += '…'
  return cut
}

export const duration = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h${String(Math.floor(s / 60) % 60).padStart(2, '0')}m`
}

const textOf = (content: unknown) =>
  typeof content === 'string'
    ? content
    : Array.isArray(content)
      ? content.flatMap(b => (b?.type === 'text' ? [b.text as string] : [])).join('\n')
      : ''

// The arguments worth showing: the command, else the path, else the whole object in one line.
export const keyArgs = (args: Record<string, unknown> = {}) => {
  const pick = args.command ?? args.path ?? args.file_path ?? args.pattern
  const text = typeof pick === 'string' ? pick : JSON.stringify(args)
  return text.length > 300 ? `${text.slice(0, 300)}…` : text
}

type Msg = { role?: string; content?: unknown; usage?: Record<string, any>; stopReason?: string; errorMessage?: string; toolCallId?: string; isError?: boolean }

// A pi session log as segments of transcript items, one per user prompt.
export const parseSession = (text: string): Segment[] => {
  const segments: Segment[] = []
  let tools = new Map<string, Extract<Item, { kind: 'tool' }>>()
  for (const entry of parseLines(text) as { type?: string; timestamp?: string; message?: Msg }[]) {
    const m = entry.message
    const at = Date.parse(entry.timestamp ?? '')
    if (entry.type === 'message' && m?.role === 'user') {
      segments.push({ items: [{ kind: 'user', text: textOf(m.content) }], usage: zero(), last: at, done: false })
      tools = new Map()
      continue
    }
    const seg = segments.at(-1)
    if (!seg) continue // before the first prompt (system), or cut off by a tail read
    if (!Number.isNaN(at)) seg.last = at
    if (entry.type !== 'message' || !m) continue
    if (m.role === 'assistant') {
      const u = m.usage ?? {}
      seg.usage.input += u.input ?? 0
      seg.usage.output += u.output ?? 0
      seg.usage.cache_read += u.cacheRead ?? 0
      seg.usage.cache_write += u.cacheWrite ?? 0
      seg.usage.cost += u.cost?.total ?? 0
      for (const b of Array.isArray(m.content) ? m.content : []) {
        if (b?.type === 'text' && b.text.trim()) seg.items.push({ kind: 'text', text: b.text.trim() })
        if (b?.type === 'toolCall') {
          const tool = { kind: 'tool' as const, id: b.id, name: b.name, args: keyArgs(b.arguments), started: at }
          tools.set(b.id, tool)
          seg.items.push(tool)
        }
      }
      if (m.stopReason === 'error') seg.items.push({ kind: 'error', text: m.errorMessage ?? 'error' })
      seg.done = m.stopReason === 'stop' || m.stopReason === 'error'
    } else if (m.role === 'toolResult') {
      const tool = tools.get(m.toolCallId ?? '')
      if (tool) Object.assign(tool, { output: textOf(m.content), isError: m.isError, ended: at })
    }
  }
  return segments
}

// The segment attempt `i` wrote: attempts sharing a session file (a retry) take its segments in order.
export const segmentOf = (segments: Segment[], sessions: (string | null | undefined)[], i: number) =>
  segments[sessions.slice(0, i).filter(s => s === sessions[i]).length]

// Output past FOLD lines shows its first FOLD and how many more there are.
export const fold = (text: string, isExpanded: boolean) => {
  const lines = text.replace(/\n+$/, '').split('\n')
  return isExpanded || lines.length <= FOLD
    ? { shown: lines.join('\n'), hidden: 0 }
    : { shown: lines.slice(0, FOLD).join('\n'), hidden: lines.length - FOLD }
}

// A tree draws only its first 100,000 characters, and the end is what to follow: drop the oldest
// items (never the prompt) until the rest fits.
export const fitTail = (items: Item[], expanded: readonly string[], budget = 90_000) => {
  const out = (it: Extract<Item, { kind: 'tool' }>) => fold(it.output ?? '', expanded.includes(it.id)).shown.length
  const size = (it: Item) => (it.kind === 'tool' ? it.args.length + out(it) : it.text.length)
  let total = items.reduce((n, it) => n + size(it), 0)
  let from = 1
  while (total > budget && from < items.length - 1) total -= size(items[from++]!)
  return { items: from > 1 ? [...items.slice(0, 1), ...items.slice(from)] : items, dropped: from - 1 }
}

export const STALL_MS = 120_000
const SPIN = [...'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏']
const VERB: Record<string, string> = { write: 'writing', edit: 'editing', read: 'reading' }

const short = (text: string) => {
  const line = text.split('\n')[0] ?? ''
  return line.length > 60 || text.includes('\n') ? `${line.slice(0, 60)}…` : line
}

// What the worker is doing, from its segment's newest entries: a tool call still without its
// result (the first of the last batch), else the model's turn.
export const doing = (seg: Segment | undefined) => {
  if (!seg) return 'starting pi'
  let i = seg.items.length
  while (i > 0 && seg.items[i - 1]!.kind === 'tool') i--
  const open = seg.items.slice(i).find(it => it.kind === 'tool' && it.output === undefined)
  if (open?.kind === 'tool') return `${VERB[open.name] ?? `running ${open.name}:`} ${short(open.args)}`
  if (seg.done) return seg.items.at(-1)?.kind === 'error' ? 'model error' : 'done'
  return 'waiting on the model'
}

// The state line: a finished run's state, or a live one's activity, a spinner frame per second,
// and stalled once nothing new was logged for STALL_MS. `since` stands in before the first entry.
// `doing` is that activity without the fallback prefix, for rows whose state word already says it.
export const activity = (live: boolean, state: string, model: string, seg: Segment | undefined, since: number, now: number) => {
  const last = seg && !Number.isNaN(seg.last) ? seg.last : since
  const idle = Math.max(0, now - last)
  if (!live) return { text: state, doing: '', idle, stalled: false, spin: '' }
  const stalled = idle >= STALL_MS
  const work = `${doing(seg)}${stalled ? ' · stalled' : ''}`
  const text = `${state === 'fell back' ? `fell back to ${model} · ` : ''}${work}`
  return { text, doing: work, idle, stalled, spin: stalled ? '!' : SPIN[Math.floor(now / 1000) % SPIN.length]! }
}
