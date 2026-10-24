---
name: secure-coding
description: Stack-neutral, OWASP-aligned secure design and implementation guide covering trust boundaries, input validation, injection prevention, cryptographic and authentication hygiene, secrets management, SSRF and path-traversal defense, and fail-closed error handling.
---

# Secure Coding & Design Practices

This skill defines the standard security engineering principles and verification criteria for **Architect**, **DEV**, and their reviewers across the Lintel delivery lifecycle. All practices are stack-neutral and apply across every runtime, language, framework, and execution environment.

---

## 1. Core Security Principles

1. **Defense in Depth**: Never rely on a single security control. Layer independent controls across perimeter validation, business logic authorization, data access parameterization, and output encoding so that a bypass in one layer is contained by the next.
2. **Least Privilege**: Every component, service identity, database account, and execution context must operate with the minimum permissions, scopes, and network reachability required to perform its explicit function.
3. **Fail-Closed Design**: When an error, timeout, ambiguous state, or missing configuration occurs during authentication, authorization, or policy evaluation, the system must deny access by default and abort the operation safely.
4. **Secure by Default**: Default configurations must enforce strict security (e.g., TLS verification enabled, debug endpoints disabled, restrictive CORS, authentication required unless explicitly marked public).
5. **Separation of Duties & Contexts**: Keep control plane and data plane concerns separated. Never mix untrusted user data with executable instructions or system control parameters.

---

## 2. Trust Boundaries & Input Validation

### 2.1 Explicit Trust Boundaries
- **Architect** must identify and document every trust boundary where execution or data flows between zones of differing privilege or trust (e.g., external client to API gateway, public webhook to worker queue, application service to external third-party API, tenant A context to shared storage).
- Treat **all** data crossing a trust boundary—including HTTP headers, query parameters, request bodies, CLI arguments, environment variables from untrusted callers, webhook payloads, and database records populated by external feeds—as untrusted until validated.

### 2.2 Strict Input Validation
- **Allowlisting over Denylisting**: Validate inputs against a strict allowlist of permitted characters, patterns, or enumerated values. Never rely on denylisting or stripping known-bad characters.
- **Schema and Type Validation**: Enforce strict structural schemas at the boundary. Reject unexpected fields (`additionalProperties: false` or equivalent strict schema enforcement) to prevent mass-assignment and parameter pollution vulnerabilities.
- **Numeric Bounds & Length Limits**: Enforce explicit minimum and maximum bounds on all integers, floating-point values, array lengths, payload byte sizes, and string lengths to prevent integer overflow/underflow, buffer exhaustion, and Denial of Service (DoS) via resource exhaustion.
- **Canonicalization Before Validation**: Decode and canonicalize inputs into their standard internal representation **once** before running validation checks. Reject double-encoded or malformed sequences immediately rather than attempting to sanitize and proceed.

---

## 3. Injection Prevention

Whenever untrusted data interacts with an interpreter, parser, or downstream subsystem, separate code from data using structured, parameterized interfaces.

### 3.1 Data Store & Query Injection (SQL, NoSQL, LDAP, Graph)
- **Parameterized Queries Only**: Always use prepared statements, parameterized queries, or strict bind variables provided by the data access driver.
- **No String Interpolation**: Never concatenate or interpolate untrusted input into query strings, filter expressions, or sort/column identifiers.
- **Identifier Allowlisting**: Where dynamic table names, column names, or sort directions cannot be parameterized by the driver, map the user-supplied value against a hardcoded, immutable allowlist of valid identifiers before query construction.

### 3.2 Command & OS Execution Injection
- **Structured Execution APIs**: Invoke external binaries using structured argument-array APIs (`execve`-style execution where the executable path and argument vector are passed as distinct elements) without invoking a command shell (`sh`, `bash`, `cmd.exe`, etc.).
- **Prohibit Shell Interpolation**: Never pass untrusted input into shell evaluation functions or construct command strings via string formatting.
- **Argument Safety**: Prefix or validate positional arguments so user-supplied values starting with `-` or `--` cannot be interpreted as command-line flags (option injection).

### 3.3 Output Encoding & Context-Aware Escaping (XSS, Template Injection)
- **Context-Specific Encoding**: Encode all untrusted data right before rendering it into an output context (HTML body, HTML attribute, URI component, CSS value, or client-side script data block).
- **Safe Template Engines**: Use auto-escaping template engines and never evaluate user input as a template string (Server-Side Template Injection).

---

## 4. Authentication, Token & Cryptographic Hygiene

### 4.1 High-Entropy Randomness
- Generate all session identifiers, CSRF tokens, password reset codes, API keys, nonces, and cryptographic salts using the platform's Cryptographically Secure Pseudo-Random Number Generator (CSPRNG).
- Never use general-purpose PRNGs, timestamps, or predictable sequences for security-sensitive values.

### 4.2 Constant-Time Comparison
- Compare HMAC signatures, API tokens, webhook secrets, and password hashes using constant-time byte comparison functions (`hmac.compare_digest` / `crypto.subtle.timingSafeEqual` equivalents) so execution duration does not leak secret bytes via timing side channels.

### 4.3 Algorithm Pinning & Token Verification
- **Algorithm Pinning**: When verifying signed tokens (such as JWTs or cryptographic signatures), explicitly specify the exact allowed algorithm(s) in the verification call. Reject the `"none"` algorithm and prevent asymmetric-to-symmetric key confusion attacks (e.g., rejecting HMAC verification when an RSA/ECDSA public key is configured).
- **Complete Claim Validation**: Always validate signature integrity **first**, followed by issuer (`iss`), audience (`aud`), expiration (`exp`), not-before (`nbf`), and subject (`sub`) claims.
- **Clock-Skew Bounds**: Enforce bounded clock-skew tolerances (typically 30–60 seconds) when evaluating `exp` and `nbf` timestamps, and ensure tokens have finite, short-lived lifetimes.

### 4.4 Password & Credential Storage
- Hash passwords using modern, memory-hard key derivation functions (Argon2id, bcrypt, or scrypt) with appropriate work factors and unique per-credential salts.
- Never store plaintext passwords or use fast cryptographic hashes (MD5, SHA-1, unsalted SHA-256) for password storage.

---

## 5. Authorization & Access Control

- **Enforce at Every Operation**: Verify both authentication (who the caller is) and authorization (whether this caller is permitted to perform this action on this specific resource) on every request.
- **Object-Level Authorization (BOLA / IDOR Prevention)**: Never trust resource identifiers supplied by the client in URLs, headers, or payloads without verifying that the authenticated principal owns or has explicit permission to access the target record.
- **Function-Level Authorization**: Protect administrative, internal, and state-mutating endpoints with explicit role/permission checks enforced by centralized middleware or policy guards.

---

## 6. Secrets Management & Data Protection

- **Zero Hardcoded Secrets**: Never commit API keys, database credentials, private keys, encryption keys, session secrets, or bearer tokens into source code, configuration files, or test fixtures.
- **Externalized Secret Injection**: Load secrets at runtime from a dedicated secrets manager, encrypted key store, or isolated environment configuration.
- **Log & Telemetry Redaction**: Never write credentials, authorization headers, session cookies, payment data, or Sensitive Personally Identifiable Information (PII) to application logs, audit trails, crash dumps, or distributed tracing spans. Implement structured log scrubbers/redaction filters for sensitive field names.
- **In-Transit & At-Rest Encryption**: Enforce modern TLS (TLS 1.2+ / TLS 1.3) with strict certificate chain and hostname verification for all network communication. Encrypt sensitive data at rest using authenticated encryption modes (such as AES-GCM or ChaCha20-Poly1305).

---

## 7. SSRF & Path-Traversal Prevention

### 7.1 Server-Side Request Forgery (SSRF) Defense
- **Avoid Arbitrary User-Supplied URLs**: Whenever possible, do not fetch arbitrary URLs supplied by untrusted users. Instead, accept an identifier or path key and construct the target URL from a hardcoded base endpoint.
- **Strict URL & Network Validation**: When outbound requests to user-influenced destinations are required:
  1. Parse the URL and enforce an explicit allowlist of schemes (`https` only) and permitted domain names.
  2. Resolve the hostname via DNS and verify that **all** resolved IP addresses are public routable addresses—explicitly block loopback (`127.0.0.0/8`, `::1`), link-local / cloud metadata (`169.254.0.0/16`, `fe80::/10`), private RFC 1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and unique local IPv6 (`fc00::/7`).
  3. Pin the validated IP address for the actual socket connection (to prevent DNS rebinding TOCTOU attacks) and disable automatic HTTP redirects (or re-validate every redirect hop against the full policy).

### 7.2 Path-Traversal & Local File Inclusion Defense
- **Avoid User-Supplied File Paths**: Map user requests to generated random identifiers (UUIDs) or database keys rather than passing user input to filesystem APIs.
- **Canonical Path Containment**: When constructing a file path from untrusted input is unavoidable:
  1. Reject inputs containing path separators (`/`, `\`), parent directory references (`..`), or null bytes (`\0`).
  2. Resolve the joined path to its canonical, absolute filesystem path (resolving any symbolic links).
  3. Verify that the canonical target path starts strictly with the canonical base directory prefix before opening, reading, writing, or deleting the file.

---

## 8. Fail-Closed Error Handling & Resilience

- **No Sensitive Data Leakage in Errors**: Return generic, structured error responses (with a correlation/request ID) to external callers on unhandled exceptions or security failures. Never expose stack traces, SQL error messages, internal filesystem paths, environment variables, or library versions in client-facing responses.
- **Atomic Cleanup on Failure**: Ensure partial failures during security-critical workflows (authentication handshakes, permission updates, multi-step transactions, temporary file creation) roll back cleanly and leave no orphaned resources or elevated sessions behind.
- **Resource & Concurrency Guards**: Enforce explicit connection, read, and execution timeouts on all network calls, file operations, and regular expression evaluations (preventing ReDoS via linear-time regex engines or strict input length caps).

---

## 9. Role-Specific Responsibilities & Review Checklist

### For Architect (Design & Specification Phase)
- [ ] Are all trust boundaries, external integrations, and privilege levels explicitly mapped in the specification?
- [ ] Are authentication mechanisms, token validation rules, and object/function-level authorization models specified for every endpoint?
- [ ] Are input schemas, numeric/length bounds, and rate/resource limits defined at system entry points?
- [ ] Is a secrets handling and PII redaction strategy defined for storage, transit, and observability?

### For DEV (Implementation Phase)
- [ ] Are all inputs crossing trust boundaries validated against strict schemas, types, bounds, and allowlists?
- [ ] Do all database queries, command executions, and output renderings use parameterized or context-encoded APIs with zero raw string interpolation?
- [ ] Are CSPRNGs, constant-time comparisons, and algorithm-pinned verification calls used for all cryptographic and token operations?
- [ ] Are outbound network calls protected against SSRF (scheme/domain allowlist, private IP blocking, redirect checks) and filesystem operations protected via canonical path containment?
- [ ] Do all error paths fail closed without leaking stack traces, secrets, or PII in responses or logs?

### For Reviewers (Architecture & Code Review)
- [ ] Verify no hardcoded secrets, tokens, or private keys exist anywhere in the diff.
- [ ] Verify every state-changing or data-fetching operation enforces both authentication and object-level authorization.
- [ ] Verify negative and boundary security cases (malformed payloads, expired/forged tokens, unauthorized resource IDs, path traversal sequences, oversized inputs) are rejected deterministically.
