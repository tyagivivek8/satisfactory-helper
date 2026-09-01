# Security policy

## Reporting a vulnerability

Use the repository's private vulnerability-reporting option under the **Security** tab when it is available. Include affected versions, reproduction steps, impact, and a minimal proof of concept.

Do not publish credentials, subscription tokens, save files, private conversations, Steam account IDs, or absolute user paths in a public issue. If private reporting is not available, open a minimal issue asking the maintainer to enable a private channel and omit all sensitive or exploitable details.

## Security boundaries

Satisfactory Helper is designed as a localhost-only application. Its key boundaries are:

- original saves are opened for discovery and copying only;
- parsing and agent tools operate on private snapshots under `.local-data`;
- the bundled MCP surface exposes read-only tools;
- credentials remain managed by the installed Codex or Claude CLI; and
- generated maps, snapshots, logs, and conversations are excluded from Git.

Please treat any regression in those boundaries as a security issue.
