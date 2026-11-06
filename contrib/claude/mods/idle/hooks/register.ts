import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, SessionCompactResult, SessionMessage, Timer } from 'claude-code'

import type { Away } from '../types'

type $ = EngineInterface

const COMPACTIONS = '/.local/state/claude-compactions'
const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000
const HOUR_MS = 60 * 60 * 1000
// shortcut: assumes the 1-hour prompt cache TTL (the mod API doesn't expose it); revisit if sessions move to another TTL.
const PING_MS = 50 * 60 * 1000
// 2 h covers a typical break.
const WARM_MS = 2 * HOUR_MS
// A rewrite of a small context costs less than pings do, so only large ones stay warm.
const WARM_MIN_TOKENS = 50_000
const MAX_HOURS = 12
const KEEPALIVE = 'keepalive: reply "ok" only'
const USAGE = 'Usage: /away <hours> (max 12), /away 1.5h, or /away off.'
const LIMIT = 'Hours must be a number over 0 and at most 12: /away 2, /away 1.5h, or /away off.'
const COMPACTED = 'Summarize in one sentence; this summary will be replaced.'
const HEADER =
  'This session is being continued from a previous conversation. The summary below covers the earlier portion of the conversation.\n\nSummary:\n'

const away = atom({ plugin: 'idle', key: 'away' } as const, {} as Away, { shape: 'away-3' })

let ping: Timer | undefined
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

// The prompt asks for <analysis> then <summary>; keep only the summary (whole reply if untagged).
export const summaryOf = (reply: string) =>
  (/<summary>([\s\S]*?)<\/summary>/.exec(reply)?.[1] ?? reply.replace(/<analysis>[\s\S]*?<\/analysis>/, '')).trim()

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
  // Keep what outlives a window: the setting, the opt-out, and when the last request ended (the cache's clock).
  await update($, away, (s) => ({ off: s.off, hours: s.hours, lastTurnEnd: s.lastTurnEnd }))
  $.ui.status(undefined)
}

const contextTokens = async ($: $) => (await $.session.usage()).context.tokens ?? 0

async function arm($: $, until: number, base: number) {
  ping?.cancel()
  const due = base + PING_MS
  const wait = Math.max(0, Math.min(due, until) - (await $.clock.now()))
  ping = $.clock.after(wait, async () => {
    ping = undefined
    const { awayUntil } = await read($, away)
    if (awayUntil === undefined) return
    if (due < awayUntil) {
      await update($, away, (s) => ({ ...s, pinging: true }))
      await $.prompt.submit({ text: KEEPALIVE })
    } else await clearAway($)
  })
}

export const register: Register = (on, options) => {
  const compaction = options.compaction === 'mod' ? 'mod' : 'engine'

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'away',
      description:
        "Keep this session's cache warm after each of your turns: /away <hours> (max 12) sets how long, /away off stops it, /away shows it. Default: 2 h for sessions over 50k tokens.",
    })
    await pruneCompactions($)

    const now = await $.clock.now()
    const { awayUntil, lastTurnEnd } = await read($, away)
    if (awayUntil !== undefined && awayUntil > now) {
      $.ui.status(`warm until ${hhmm(awayUntil)}`)
      await arm($, awayUntil, lastTurnEnd ?? now)
    } else if (awayUntil !== undefined) {
      await clearAway($)
    }
    return next(e)
  })

  on('command.run', { command: 'away' }, async ($, e) => {
    const args = e.args.trim()
    if (args === 'off') {
      await clearAway($)
      await update($, away, (s) => ({ ...s, off: true }))
      return { text: "This session won't be kept warm. /away <hours> turns it back on." }
    }
    if (args === '') {
      const now = await $.clock.now()
      const { awayUntil, hours, off } = await read($, away)
      const open =
        awayUntil !== undefined && awayUntil > now ? `; now until ${hhmm(awayUntil)}` : ''
      if (off) {
        return { text: `Not kept warm (off). /away <hours> turns it back on.\n${USAGE}` }
      }
      if (hours !== undefined) {
        return { text: `Kept warm ${hours} h after each turn (set with /away)${open}.\n${USAGE}` }
      }
      if ((await contextTokens($)) >= WARM_MIN_TOKENS) {
        return {
          text: `Kept warm ${WARM_MS / HOUR_MS} h after each turn (default for sessions over 50k tokens)${open}.\n${USAGE}`,
        }
      }
      return {
        text: `Not kept warm: the session is under 50k tokens. /away <hours> keeps it warm anyway.\n${USAGE}`,
      }
    }

    const match = /^(\d+(?:\.\d+)?)(?:h)?$/i.exec(args)
    const hours = match ? Number(match[1]) : NaN
    if (!match || !(hours > 0 && hours <= MAX_HOURS)) return { text: LIMIT }

    const now = await $.clock.now()
    const until = now + hours * HOUR_MS
    const { lastTurnEnd } = await update($, away, (s) => ({
      ...s,
      awayUntil: until,
      hours,
      off: undefined,
    }))
    $.ui.status(`warm until ${hhmm(until)}`)
    await arm($, until, lastTurnEnd ?? now)
    return {
      text: `This session stays warm ${hours} h after each of your turns (now until ${hhmm(until)}).`,
    }
  })

  on('turn.complete', async ($, e, next) => {
    const now = await $.clock.now()
    const { pinging, awayUntil, off, hours } = await update($, away, (s) => ({
      ...s,
      lastTurnEnd: now,
    }))
    if (pinging) {
      await update($, away, (s) => ({ ...s, pinging: undefined }))
      if (awayUntil !== undefined) await arm($, awayUntil, now)
      else await clearAway($)
    } else if (off) {
      await clearAway($)
    } else {
      const length =
        hours !== undefined
          ? hours * HOUR_MS
          : (await contextTokens($)) >= WARM_MIN_TOKENS
            ? WARM_MS
            : undefined
      if (length === undefined) {
        await clearAway($)
      } else {
        const until = now + length
        await update($, away, (s) => ({ ...s, awayUntil: until }))
        $.ui.status(`warm until ${hhmm(until)}`)
        await arm($, until, now)
      }
    }
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
    let why = `the kept tail has ${tail.length} message(s)`
    if (tail.length >= 2) {
      const reply = await $.model
        .fork({ prompt: withExtra(text, own) })
        .catch((err: unknown) => ({ isAnswered: false as const, reason: String(err) }))
      if (reply.isAnswered) {
        let r: SessionCompactResult | undefined
        try {
          r = await next({ ...e, messages: tail, instructions: COMPACTED })
          why = r.skip === undefined ? why : `the engine skipped the tail: ${r.skip}`
        } catch (err) {
          why = `the engine refused the tail: ${String(err)}`
          r = undefined
        }
        if (r !== undefined && r.skip === undefined) {
          const summaryText = summaryOf(reply.text)
          await saveSummary($, summaryText)
          const summary: SessionMessage = {
            role: 'user',
            text: HEADER + summaryText,
            toolUses: [],
          }
          return { ...r, messages: [summary, ...tail] }
        }
      } else {
        why = `the fork gave no summary: ${'reason' in reply ? reply.reason : 'no reason'}`
      }
    }

    $.ui.log(`idle: engine wrote this summary; ${why}`)
    const r = await next({ ...e, instructions })
    await saveCompacted($, r)
    return r
  })

  // Any prompt that is not this mod's own ping means the person is back: it
  // clears the compaction status, but leaves the window and its timer alone.
  on('prompt.submit', async ($, e, next) => {
    if (!(e.origin?.kind === 'plugin' && e.origin.name === 'idle')) {
      const now = await $.clock.now()
      const { awayUntil } = await read($, away)
      $.ui.status(awayUntil !== undefined && awayUntil > now ? `warm until ${hhmm(awayUntil)}` : undefined)
    }
    return next(e)
  })
}
