export type Away = {
  awayUntil?: number
  hours?: number
  lastTurnEnd?: number
  pinging?: boolean
  off?: boolean
}

declare module 'claude-code' {
  interface PluginState {
    idle: { away: Shaped<Away> }
  }
}
