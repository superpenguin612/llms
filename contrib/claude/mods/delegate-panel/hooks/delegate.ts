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

export type Item =
  | { kind: 'user'; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'error'; text: string }
  | { kind: 'tool'; id: string; name: string; args: string; output?: string; isError?: boolean }

// One pi prompt and everything after it: an attempt, or a retry appended to the same file.
export type Segment = { items: Item[]; usage: Usage }

export const FOLD = 6

const zero = (): Usage => ({ input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 })

const parseLines = (text: string): unknown[] =>
  text.split('\n').flatMap(line => {
    try {
      return line.trim() ? [JSON.parse(line)] : []
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
  for (const entry of parseLines(text) as { type?: string; message?: Msg }[]) {
    const m = entry.message
    if (entry.type !== 'message' || !m) continue
    if (m.role === 'user') {
      segments.push({ items: [{ kind: 'user', text: textOf(m.content) }], usage: zero() })
      tools = new Map()
      continue
    }
    const seg = segments.at(-1)
    if (!seg) continue // before the first prompt (system), or cut off by a tail read
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
          const tool = { kind: 'tool' as const, id: b.id, name: b.name, args: keyArgs(b.arguments) }
          tools.set(b.id, tool)
          seg.items.push(tool)
        }
      }
      if (m.stopReason === 'error') seg.items.push({ kind: 'error', text: m.errorMessage ?? 'error' })
    } else if (m.role === 'toolResult') {
      const tool = tools.get(m.toolCallId ?? '')
      if (tool) Object.assign(tool, { output: textOf(m.content), isError: m.isError })
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
