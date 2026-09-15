# AI provider contract

Supported adapters are Codex, Claude Code, and Gemini CLI. Detect the executable, version, and required safe noninteractive flags. Prefer Codex, then Claude, then Gemini when no provider is chosen. An unavailable provider does not block scanner-only reporting.

## Input

Supply only the affected extension's sanitized source, its module compatibility record, scanner findings, permitted project-relative roots, proposal schema, and registered verification IDs. Do not expose settings, SQL, uploads, credentials, browser sessions, arbitrary commands, MCP servers, or unrelated source.

## Output

Require one JSON object with `summary`, `findings`, `changes`, `steps`, and `limitations`. Each change includes `path`, `beforeSha256`, complete replacement content in `after`, a finding, rationale, and verification check IDs. Provider-selected steps must be registered IDs. The hybrid patch flow normally accepts file changes and no provider-authored commands.

Record provider, CLI version, model, duration, usage when available, and a categorized failure: unavailable capability, authentication, rate limit, malformed output, forbidden scope, stale source, or deterministic validation failure.

Run providers with read-only or plan permissions. Disable shell mutation, writes, browser tools, MCP servers, and arbitrary command configuration. Provider output cannot mark a test passed, authorize a batch, or override missing evidence. Preserve failed output as diagnostic evidence and keep Gate 1 blocked.
