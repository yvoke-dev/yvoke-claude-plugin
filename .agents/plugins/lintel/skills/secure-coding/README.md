# Secure Coding Method Skill (`secure-coding`)

## Overview

The `secure-coding` method skill provides a stack-neutral, OWASP-aligned secure design and implementation reference for **Architect**, **DEV**, and their reviewers across the Lintel delivery workflow.

## What This Skill Covers

1. **Core Security Principles**: Defense in depth, least privilege, fail-closed design, secure defaults, and separation of control/data contexts.
2. **Trust Boundaries & Input Validation**: Identifying trust boundaries, allowlisting over denylisting, strict schema/type validation, numeric bounds and payload size limits, and canonicalization prior to validation.
3. **Injection Prevention**: Parameterized queries for data stores, structured argument-array command execution without shell interpolation, and context-aware output encoding.
4. **Authentication, Token & Cryptographic Hygiene**: CSPRNG entropy, constant-time secret comparison, cryptographic algorithm pinning, complete token claim and clock-skew/expiration validation, and memory-hard password hashing.
5. **Authorization & Access Control**: Per-operation authentication and authorization checks, plus Object-Level (BOLA/IDOR) and Function-Level authorization enforcement.
6. **Secrets Management & Data Protection**: Zero hardcoded credentials, runtime secret injection, strict PII/credential log redaction, and authenticated encryption in transit and at rest.
7. **SSRF & Path-Traversal Prevention**: Scheme/domain allowlisting, private/loopback/metadata IP range blocking, DNS rebinding protection, and canonical base-directory path containment.
8. **Fail-Closed Error Handling**: Redacted client-facing error responses, correlation IDs without stack trace leakage, atomic rollback on failure, and ReDoS/timeout guards.

## Usage in Lintel

Assign `lintel:secure-coding` in `.lintel/config.jsonc` under `skills` for roles that design, implement, or review security-sensitive components (typically `architect`, `dev`, and `qa`).
