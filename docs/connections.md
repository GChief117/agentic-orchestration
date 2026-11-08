# Connection and permissions

Status: public-facing design requirements; these mechanisms are not implemented by this repository.

## Target identity and transport

Use a stable logical target ID in plans. Resolve live addresses, usernames, keys, and trust material from private local configuration. Before dispatch, verify the expected remote identity through the selected transport's trust mechanism, such as SSH host-key verification or TLS certificate verification.

The initial transport profiles are an authenticated local service and SSH. The public examples do not choose a live endpoint or copy a private bridge's settings. Loopback placement or an encrypted transport does not replace authorization.

## Permissions

Evaluate access using the authenticated principal, target, capability, resource scope, and current policy revision. Discovery reports capability; permission checks determine whether this principal may use it now. Reject stale, expired, or revoked sessions at the execution boundary.

A scoped worker session should receive only the authority required for its assigned task. Administrative operations such as credential rotation, service restart, installation changes, or upgrade activation require distinct authority from ordinary work.

## Session lifecycle

Proposed lifecycle: create -> active -> expired, revoked, or closed. Renewal extends an active session under policy; it does not silently revive a revoked session. A heartbeat demonstrates liveness only, not useful progress or successful execution.

Document what happens to jobs, browser contexts, desktop leases, and temporary files on expiry, disconnect, cancellation, and shutdown. Export needed artifacts before deleting an ephemeral environment. Cleanup failures must remain observable.

## Resource boundaries

- File scopes use canonical paths and account for symlink escapes and overlapping roots.
- Browser scopes distinguish independent contexts from existing user sessions; credentials are entered through an authorized sign-in flow rather than copied implicitly.
- Native desktop input requires exclusive ownership or a separately isolated environment. Human reclaim should stop further automated input and invalidate stale ownership.
- Terminal authority is explicit. Broad shell access can escape application-level file and browser restrictions unless the operating environment enforces those restrictions too.
- Agent providers authenticate separately; a remote machine connection is not proof that a model account is configured or authorized.

These are adapter requirements to test, not claims about controls already present in this repository.

## Credentials and records

Plans, examples, source files, and receipts contain references to secrets, never live secret values. Logs should omit credentials, authentication pages, session tokens, and sensitive input values. Screenshots and arbitrary page text can still contain private information; collect only what the task requires.

Private endpoint addresses, pairing bundles, browser profiles, mailbox contents, environment files, and execution logs do not belong in repository examples. Any future import of implementation code must be reviewed separately for licensing, configuration, and secret leakage.

## Connection loss

When a transport disconnects, stop new dispatch to that target until identity and access are revalidated. Reconcile active operation IDs after reconnection. Do not automatically replay clicks, uploads, or terminal commands, and do not silently fall back to the controlling machine.
