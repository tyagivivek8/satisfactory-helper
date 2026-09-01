# Contributing

Thanks for improving Satisfactory Helper. Bug reports, focused pull requests, save-parser compatibility fixes, and practical planning improvements are welcome for noncommercial use.

## Before opening an issue

- Search existing issues first.
- Include the Satisfactory build number, provider, selected model, and the smallest prompt that reproduces the problem.
- Remove usernames, Steam account IDs, absolute local paths, conversation contents, and any other personal data from logs.
- Never attach a real `.sav` file publicly. If a parser problem cannot be reproduced without one, coordinate a private transfer with a maintainer first.

## Development setup

Requirements and first-run instructions are in [README.md](README.md). Install dependencies and start the development servers with:

```powershell
pnpm setup
pnpm dev
```

Run the complete check before proposing a change:

```powershell
pnpm check
```

This project intentionally does not modify original Satisfactory saves. Changes that add a write path to the discovered save root, weaken snapshot isolation, expose the server beyond localhost by default, or send private runtime data to an unrelated service will not be accepted.

## Vendored SatisfactoryMCP changes

Keep edits under `vendor/SatisfactoryMCP` focused and document them in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Preserve `vendor/SatisfactoryMCP/LICENSE` and its Required Notice. When practical, contribute generally useful fixes upstream as well.

By contributing, you agree that your contribution may be distributed under the repository's PolyForm Noncommercial 1.0.0 terms and any applicable third-party terms.
