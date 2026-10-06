// The yvoke mod's entry point. Each feature lives in its own src/ module and adds one line here, so
// parallel tasks do not collide.
import type { Register } from 'claude-code'
import { registerSession } from '../src/session'

export const register: Register = (on) => {
  registerSession(on)
}
