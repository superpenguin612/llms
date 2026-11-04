export type View = { task?: string; attempt?: number }

declare module 'claude-code' {
  interface PluginState {
    'delegate-panel': { view: Shaped<View>; expanded: Shaped<string[]>; following: Shaped<boolean> }
  }
}
