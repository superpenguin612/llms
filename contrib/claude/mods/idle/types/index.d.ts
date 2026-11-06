export type Away = { awayUntil?: number }

declare module 'claude-code' {
  interface PluginState {
    idle: { away: Shaped<Away> }
  }
}
