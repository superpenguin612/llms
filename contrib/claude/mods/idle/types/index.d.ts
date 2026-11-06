export type Away = {
  awayUntil?: number
  kind?: 'away' | 'warm'
  lastTurnEnd?: number
  pinging?: boolean
}

declare module 'claude-code' {
  interface PluginState {
    idle: { away: Shaped<Away> }
  }
}
