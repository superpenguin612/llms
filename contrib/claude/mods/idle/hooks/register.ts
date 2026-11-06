import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Away } from '../types'

type $ = EngineInterface

const COMPACTIONS = '/.local/state/claude-compactions'
const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000

const away = atom({ plugin: 'idle', key: 'away' } as const, {} as Away, { shape: 'away-1' })

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

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'away',
      description: "Keep this session's cache warm until you're back: /away <hours> (max 12), /away off",
    })
    await pruneCompactions($)
    return next(e)
  })
}
