# Electron Architectural Design

Architectural and security practices for Electron desktop applications under Lintel. When designing features for an Electron project, the **Architect** and **Architect-Reviewer** must enforce strict process separation, least-privilege preload bridges, typed IPC contracts, and headless-friendly testability.

---

## 1. Three-Process Isolation Model

Electron applications consist of three distinct execution environments with different privilege levels. Never blur their responsibilities in `design.md` or `contract.md`:

| Process / Layer | Runtime Environment | Responsibilities | Forbidden Dependencies |
| :--- | :--- | :--- | :--- |
| **Pure Domain (`src/lib/` or `src/shared/`)** | Pure TypeScript (runtime-agnostic) | Domain logic, data validation, state machines, formatting, shared IPC payload types & channel constants | `electron`, `fs`, `child_process`, DOM (`window`, `document`), UI frameworks |
| **Main Process (`src/main/`)** | Node.js + Electron Main APIs (`app`, `BrowserWindow`, `ipcMain`, `dialog`, `Menu`) | Window lifecycle, native OS integration, filesystem/SQLite/keychain access, registering `ipcMain.handle` listeners | DOM globals (`window`, `document`), React/Vue/UI components, `ipcRenderer` |
| **Preload Script (`src/preload/`)** | Sandboxed bridge (`contextBridge`, `ipcRenderer`) | Exposing a narrow, typed capability API onto `window.<apiKey>` via `contextBridge.exposeInMainWorld` | Direct raw exposure of `ipcRenderer`, Node built-ins (when sandboxed), heavy business logic |
| **Renderer Process (`src/renderer/`)** | Chromium Browser (DOM, Web APIs, UI framework) | Rendering UI, handling user interaction, invoking capabilities exclusively via `window.<apiKey>` | `electron`, `fs`, `path`, `os`, `child_process`, direct Node.js APIs |

---

## 2. Mandatory `BrowserWindow` Security Baseline

Every `BrowserWindow` (and `WebContentsView`) configured in `src/main/` must enforce the following `webPreferences` invariants unless an explicit, security-reviewed exception is documented in `design.md`:

```ts
const mainWindow = new BrowserWindow({
  webPreferences: {
    preload: path.join(__dirname, "preload.js"),
    contextIsolation: true,   // MANDATORY: isolates preload execution context from renderer window
    nodeIntegration: false,   // MANDATORY: prevents renderer scripts from accessing Node.js primitives
    sandbox: true,            // MANDATORY: runs renderer/preload in Chromium OS-level sandbox
    webSecurity: true,        // Never disable webSecurity in production
    allowRunningInsecureContent: false,
  },
});
```

- **Never expose raw `ipcRenderer`:** Preload scripts must never pass `ipcRenderer` or `ipcRenderer.invoke` directly into `contextBridge.exposeInMainWorld`. Always wrap each allowed operation in a dedicated function that validates its arguments before calling `ipcRenderer.invoke(CHANNEL, payload)`.
- **Navigation & Window Creation Guards:** In `src/main/`, restrict arbitrary navigation (`will-navigate`) and popup creation (`setWindowOpenHandler`) so untrusted external URLs open in the user's default OS browser (`shell.openExternal` after protocol allowlist validation for `https:`) rather than spawning privileged `BrowserWindow` instances.

---

## 3. Typed IPC Contracts (`shared/phase-N/contract.md`)

All communication across the Main $\leftrightarrow$ Renderer boundary must be modeled in `contract.md` with shared TypeScript types so Main handlers, Preload bridges, and Renderer callers stay type-checked in lockstep.

### 3.1 Request-Response via `ipcMain.handle` / `ipcRenderer.invoke`
- Prefer asynchronous two-way `ipcMain.handle(channel, handler)` paired with `ipcRenderer.invoke(channel, request)` over one-way `ipcRenderer.send` / `event.reply` pairs, and **never** use synchronous `event.returnValue` (`sendSync`), which blocks the UI thread.
- Define channel names as `const` string literals and define a typed channel map in a shared contract module (e.g., `src/shared/ipc-contract.ts`):
  ```ts
  export const IPC_CHANNELS = {
    VAULT_UNLOCK: "vault:unlock",
    VAULT_LOCK: "vault:lock",
  } as const;

  export type VaultUnlockRequest = {
    readonly passphrase: string;
  };

  export type VaultUnlockResponse =
    | { readonly ok: true; readonly unlockedAtMs: number }
    | { readonly ok: false; readonly code: "INVALID_PASSPHRASE" | "LOCKED_OUT"; readonly message: string };

  export interface DesktopBridgeApi {
    readonly unlockVault: (req: VaultUnlockRequest) => Promise<VaultUnlockResponse>;
    readonly lockVault: () => Promise<void>;
  }
  ```
- Augment the Renderer's global `Window` interface in a shared declaration or renderer ambient type so UI code gets strict autocomplete on `window.desktopApi` without `any` casts.

### 3.2 Untrusted Input Validation in Main Handlers
- Treat every IPC payload arriving in `ipcMain.handle` as **untrusted input** (equivalent to an HTTP POST body), because a compromised renderer could send malformed payloads.
- Validate payload shapes, path traversal boundaries (reject `..` or paths outside approved workspace/appData directories), and sender frame identity (`event.senderFrame`) inside a pure validator in `src/lib/` or at the top of the Main handler before touching the OS or filesystem.

### 3.3 Serializable IPC Payloads Only
- Electron's Structured Clone Algorithm serializes arguments across IPC. Do not pass class instances with methods, functions, DOM nodes, or Node `Buffer`/stream handles across `contextBridge`.
- Return plain `readonly` records and discriminated unions (`{ ok: true, value } | { ok: false, code, message }`) across IPC rather than relying on thrown custom `Error` subclasses, because custom error properties (`code`, `cause`) are stripped during IPC serialization.

---

## 4. Designing for Testability & Lintel Stubs

Testing a full Electron binary in every test tier is slow and brittle. Design Electron features so each layer can be verified cleanly at the right test tier in `tests/` and `e2e/`:

1. **Extract Main-Process Handlers into Pure/Injectable Services:**
   - Do not embed business logic directly inside `ipcMain.handle("channel", async (_event, arg) => { ... })` closures inside `app.whenReady()`.
   - Instead, export handler functions or service classes (e.g., `export async function handleVaultUnlock(req: VaultUnlockRequest, deps: VaultDeps): Promise<VaultUnlockResponse>`) from a module in `src/main/` or `src/lib/`.
   - Wire `ipcMain.handle` as a thin 1-line adapter calling `handleVaultUnlock`.
2. **Stub Files (`## Stub files` in `design.md`):**
   - Export all shared IPC types, channel constants, preload bridge interfaces, and extracted handler functions in the stub files.
   - Stub handler functions must compile cleanly under `tsc --noEmit` and throw `new Error("Not implemented")` so `lintel lock` fails with `exit 1` (assertion/runtime failure) and never `exit 2` (compile/import failure).
3. **Renderer Decoupling from Electron Runtime:**
   - Design Renderer UI components and hooks to access `window.<apiKey>` (or accept the bridge interface via React Context / dependency injection) so unit and integration tests in `tests/` can provide a mock `DesktopBridgeApi` on `globalThis.window` without launching Electron.
4. **Test Outline Mapping (`## Test outline` in `design.md`):**
   - **`unit`:** Pure domain logic in `src/lib/`, IPC payload validators, and extracted Main handler functions with injected mock dependencies.
   - **`integration`:** Multi-module Main + Preload IPC wiring, local SQLite/filesystem persistence adapters (using temporary directories), or Renderer + mocked `DesktopBridgeApi` flows.
   - **`e2e`:** Full application window lifecycle and end-to-end user journeys across Renderer $\rightarrow$ Preload $\rightarrow$ Main in `e2e/` (or `- e2e: none: <justification>` when changes are strictly confined to pure internal helpers).
