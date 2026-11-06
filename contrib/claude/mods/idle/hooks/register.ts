import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, SessionCompactResult, SessionMessage, Timer } from 'claude-code'

import type { Away } from '../types'

type $ = EngineInterface

const COMPACTIONS = '/.local/state/claude-compactions'
const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000
const HOUR_MS = 60 * 60 * 1000
// shortcut: assumes the 1-hour prompt cache TTL (the mod API doesn't expose it); revisit if sessions move to another TTL.
const PING_MS = 50 * 60 * 1000
const MAX_HOURS = 12
const KEEPALIVE = 'keepalive: reply "ok" only'
const USAGE = 'Usage: /away <hours> (max 12), /away 1.5h, or /away off.'
const LIMIT = 'Hours must be a number over 0 and at most 12: /away 2, /away 1.5h, or /away off.'
const COMPACTED = 'Summarize in one sentence; this summary will be replaced.'
const HEADER =
  'This session is being continued from a previous conversation. The summary below covers the earlier portion of the conversation.\n\nSummary:\n'

const away = atom({ plugin: 'idle', key: 'away' } as const, {} as Away, { shape: 'away-1' })

let ping: Timer | undefined
let lastTurnEnd: number | undefined
let prompt: string | undefined

const hhmm = (ms: number) => {
  const at = new Date(ms)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

const stamp = (ms: number) => {
  const at = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}${pad(at.getMonth() + 1)}${pad(at.getDate())}-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`
}

const slug = (cwd: string) => cwd.replace(/^\//, '').replace(/\//g, '-')

const withPerson = (text: string, own: string) =>
  own === '' ? text : `The user asked to keep or stress: ${own}\n\n${text}`

const withExtra = (text: string, own: string) =>
  own === '' ? text : `${text}\n\nThe user asked to keep or stress: ${own}`

// The last prompt and everything after it, or from the second-to-last real prompt.
function tailOf(messages: readonly SessionMessage[]) {
  const prompts: number[] = []
  messages.forEach((message, index) => {
    const isPrompt =
      message.role === 'user' && !message.toolResults?.length && message.text.trim() !== ''
    if (isPrompt) prompts.push(index)
  })
  if (prompts.length === 0) return []
  const start = prompts.length >= 2 ? prompts[prompts.length - 2]! : prompts[prompts.length - 1]!
  return messages.slice(start)
}

async function summaryPrompt($: $) {
  prompt ??= await $.fs.read(`${$.plugin.root}/hooks/summary-prompt.md`)
  return prompt
}

async function saveSummary($: $, text: string) {
  const home = await $.env.get('HOME')
  if (!home) return
  const now = await $.clock.now()
  const cwd = await $.session.cwd()
  await $.fs.write(`${home}${COMPACTIONS}/${stamp(now)}-${slug(cwd)}.md`, text)
  $.ui.status(`compacted at ${hhmm(now)}`)
}

async function saveCompacted($: $, result: SessionCompactResult) {
  const summary = result.messages?.[0]?.text
  if (summary !== undefined) await saveSummary($, summary)
}

export function isStale(mtimeMs: number, now: number) {
  return now - mtimeMs > PRUNE_AFTER_MS
}

async function pruneCompactions($: $) {
  const home = await $.env.get('HOME')
  if (!home) return
  const dir = `${home}${COMPACTIONS}`
  const entries = await $.fs.list(dir).catch(() => [])
  const now = await $.clock.now()
  for (const entry of entries) {
    if (entry.kind !== 'file' || !isStale(entry.mtimeMs, now)) continue
    await $.process.run(['rm', '-f', `${dir}/${entry.name}`]).catch(() => undefined)
  }
}

async function clearAway($: $) {
  ping?.cancel()
  ping = undefined
  await update($, away, () => ({}))
  $.ui.status(undefined)
}

async function arm($: $, until: number, base: number) {
  ping?.cancel()
  const due = base + PING_MS
  const wait = Math.max(0, Math.min(due, until) - (await $.clock.now()))
  ping = $.clock.after(wait, async () => {
    ping = undefined
    const { awayUntil } = await read($, away)
    if (awayUntil === undefined) return
    if (due < awayUntil) await $.prompt.submit({ text: KEEPALIVE })
    else await clearAway($)
  })
}

export const register: Register = (on, options) => {
  const compaction = options.compaction === 'mod' ? 'mod' : 'engine'

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'away',
      description: "Keep this session's cache warm until you're back: /away <hours> (max 12), /away off",
    })
    await pruneCompactions($)
    return next(e)
  })

  on('command.run', { command: 'away' }, async ($, e) => {
    const args = e.args.trim()
    if (args === 'off') {
      await clearAway($)
      return { text: 'Away cancelled.' }
    }
    if (args === '') {
      if ((await read($, away)).awayUntil === undefined) return { text: USAGE }
      await clearAway($)
      return { text: 'Away cancelled.' }
    }

    const match = /^(\d+(?:\.\d+)?)(?:h)?$/i.exec(args)
    const hours = match ? Number(match[1]) : NaN
    if (!match || !(hours > 0 && hours <= MAX_HOURS)) return { text: LIMIT }

    const now = await $.clock.now()
    const until = now + hours * HOUR_MS
    await update($, away, () => ({ awayUntil: until }))
    $.ui.status(`away until ${hhmm(until)}`)
    await arm($, until, lastTurnEnd ?? now)
    return { text: `Away until ${hhmm(until)}: pinging every 50 min to keep the cache warm.` }
  })

  on('turn.complete', async ($, e, next) => {
    lastTurnEnd = await $.clock.now()
    const { awayUntil } = await read($, away)
    if (awayUntil !== undefined) await arm($, awayUntil, lastTurnEnd)
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    if (compaction === 'engine' && e.trigger === 'precompute') return next(e)
    if (compaction === 'mod' && e.trigger === 'precompute') {
      return { skip: 'idle mod writes the summary' }
    }

    const text = await summaryPrompt($)
    const own = e.instructions?.trim() ?? ''
    const instructions = withPerson(text, own)

    if (compaction === 'engine') {
      const r = await next({ ...e, instructions })
      await saveCompacted($, r)
      return r
    }

    const tail = tailOf(e.messages)
    if (tail.length >= 2) {
      const reply = await $.model
        .fork({ prompt: withExtra(text, own) })
        .catch(() => undefined)
      if (reply?.isAnswered) {
        let r: SessionCompactResult | undefined
        try {
          r = await next({ ...e, messages: tail, instructions: COMPACTED })
        } catch {
          r = undefined
        }
        if (r !== undefined && r.skip === undefined) {
          await saveSummary($, reply.text)
          const summary: SessionMessage = {
            role: 'user',
            text: HEADER + reply.text,
            toolUses: [],
          }
          return { ...r, messages: [summary, ...tail] }
        }
      }
    }

    const r = await next({ ...e, instructions })
    await saveCompacted($, r)
    return r
  })

  // Any prompt that is not this mod's own ping means the person is back: it
  // cancels away and clears the compaction status.
  on('prompt.submit', async ($, e, next) => {
    if (!(e.origin?.kind === 'plugin' && e.origin.name === 'idle')) await clearAway($)
    return next(e)
  })
}
