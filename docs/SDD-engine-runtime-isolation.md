# SDD: Engine Runtime Status and Interaction Isolation

## Problem

The UI historically treated an engine as usable when its CLI was installed and
the account was logged in. That misses runtime constraints such as Claude Code
quota exhaustion: the tool can be installed and authenticated, but execution
still fails.

## Status Layers

Engine state is separated into layers:

- `installed`: the CLI binary can be spawned.
- `loggedIn`: the CLI account/auth state is valid.
- `quota`: current quota state when measurable: `ok`, `low`, `exhausted`, or
  `unknown`.
- `runnable`: the engine can be used for a new task right now.

`available` remains in the API for compatibility, but it now means the same as
`runnable`.

## Runtime States

- `ready`: installed, logged in, and no blocking quota/runtime condition.
- `quota_low`: runnable, but close to a usage limit.
- `quota_exhausted`: installed and logged in, but blocked by usage limits.
- `not_logged_in`: installed but not authenticated.
- `not_installed`: CLI is missing.
- `check_timeout`: local CLI status check timed out.
- `runtime_error`: the frontend observed a recent blocking execution failure.
- `unknown`: state could not be verified.

## Interaction Isolation

Read/edit operations stay available. Runtime operations require a runnable
engine:

- Chat send
- Team chat
- Team execute
- Team run
- Project planning

Settings and agent definitions remain editable even when an engine is not
runnable. The UI shows the blocked state instead of hiding the option, because
engine choice is a preference/configuration, not always an immediate execution
request.

When `engineMode` is locked to one engine, runtime actions are blocked if that
engine is not runnable. When `engineMode` is `both`, the backend availability
gate may fall back to the other runnable engine; the frontend warns that the
selected engine is currently blocked and that execution may fall back.

## User Feedback

Blocked controls must explain:

- Which engine is blocked.
- Why it is blocked.
- What the user can do next: wait for reset, log in, install, or switch engine
  scope/default.

Quota failures observed during streaming are reflected back into the local
runtime state immediately, then a forced engine-status refresh is requested.
