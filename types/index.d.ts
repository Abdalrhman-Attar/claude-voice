export type Sentence = { text: string; para: number; line?: number }
export type Reply = { id: string; sentences: Sentence[] }
export type SpeakSettings = {
  voice: string
  speed: number
  muted: boolean
  awayOnly: boolean
  summarize: boolean
  live: boolean
  workUpdates: boolean // speak text between tool calls, not only the final reply
  projectVoices: boolean
  mutedUntil: number // epoch ms; a timed mute holds until then
}
export type Playback = { mine: boolean; ref: string | null; index: number | null; state: 'playing' | 'paused' | 'idle' }

declare module 'claude-code' {
  interface PluginState {
    'speak-pane': { replies: Reply[]; view: number; top: number; playback: Playback; prefs: SpeakSettings }
  }
}
