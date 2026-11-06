import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

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

const away = atom({ plugin: 'idle', key: 'away' } as const, {} as Away, { shape: 'away-1' })

let ping: Timer | undefined
let lastTurnEnd: number | undefined

const hhmm = (ms: number) => {
  const at = new Date(ms)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
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
  const wait = Math.max(0, due - (await $.clock.now()))
  ping = $.clock.after(wait, async () => {
    ping = undefined
    const { awayUntil } = await read($, away)
    if (awayUntil === undefined) return
    if (due < awayUntil) await $.prompt.submit({ text: KEEPALIVE })
    else await clearAway($)
  })
}

export const register: Register = on => {
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

  // Any prompt that is not this mod's own ping means the person is back.
  on('prompt.submit', async ($, e, next) => {
    if (!(e.origin?.kind === 'plugin' && e.origin.name === 'idle')) await clearAway($)
    return next(e)
  })
}
