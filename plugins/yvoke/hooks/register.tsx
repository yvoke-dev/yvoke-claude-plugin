// The yvoke mod's entry point. Each feature lives in its own src/ module and adds one line here, so
// parallel tasks do not collide.
import type { Register } from 'claude-code'
import { registerPolicy } from '../src/policy'
import { registerSession } from '../src/session'
import { registerSetup } from '../src/setup'
import { registerTurnLimits } from '../src/turn-limits'
import { registerSystemPrompt } from '../src/system-prompt'

export const register: Register = (on, options) => {
  // Setup first: its /yvoke hook sits above the session's, so it loads the lists once the session's has
  // made this a Yvoke session (the session's hook answers without calling further down).
  registerSetup(on, options)
  registerSession(on)
  registerPolicy(on)
  registerTurnLimits(on, options)
  registerSystemPrompt(on)
}
