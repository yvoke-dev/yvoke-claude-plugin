// The yvoke mod's entry point. Each feature lives in its own src/ module and adds one line here, so
// parallel tasks do not collide. Nothing is registered yet, so every event reaches the engine unchanged.
import type { Register } from 'claude-code'

export const register: Register = () => {}
