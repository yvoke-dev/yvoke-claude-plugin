// The yvoke mod's entry point. Each feature lives in its own src/ module and adds one line here, so
// parallel tasks do not collide. Nothing is registered yet, so every event reaches the engine unchanged.
import type { Register } from 'claude-code'

// P0-02 proof only: a forbidden $.fs call, which CI must refuse. Reverted in the next commit.
export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.fs.read('/etc/hosts')
    return next(e)
  })
}
