// The yvoke mod's entry point. Each feature lives in its own src/ module and adds one line here, so
// parallel tasks do not collide.
import type { Register } from 'claude-code'
import { registerPolicy } from '../src/policy'
import { registerSession } from '../src/session'
import { registerTurnLimits } from '../src/turn-limits'
import { registerSystemPrompt } from '../src/system-prompt'

export const register: Register = (on, options) => {
  registerSession(on)
  registerPolicy(on)
  registerTurnLimits(on, options)
  registerSystemPrompt(on)
}
