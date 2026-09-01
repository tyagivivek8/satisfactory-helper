# Third-party notices

## SatisfactoryMCP

Satisfactory Helper vendors and modifies source from [SatisfactoryMCP](https://github.com/lukszi/SatisfactoryMCP), based on upstream revision `ade73e6c4736937eb49cc54364def7d6b30873d6`.

Required Notice: Copyright Lukas Szimtenings (https://github.com/lukszi/SatisfactoryMCP)

SatisfactoryMCP is licensed under the PolyForm Noncommercial License 1.0.0. The upstream terms and required notice are preserved verbatim in [`vendor/SatisfactoryMCP/LICENSE`](vendor/SatisfactoryMCP/LICENSE).

Local modifications currently:

- report the best extractor rate unlocked in the current world for untapped resource nodes;
- preserve the actual installed extractor tier when a node is already tapped; and
- add regression coverage for those rate-selection rules.

The vendored subset contains the runtime source and generation tools needed by Satisfactory Helper. Upstream development notebooks, tests, and player-derived reference-world fixtures are intentionally excluded. The remaining source stays attributable to its upstream contributors. The root `LICENSE` applies to Satisfactory Helper's original code and documentation; it does not replace third-party notices or ownership.

## OpenAI Codex CLI

The self-contained Windows release includes the official native [OpenAI Codex CLI](https://github.com/openai/codex) so players can use ChatGPT subscription sign-in without installing Node.js. Codex CLI is Copyright 2025 OpenAI and is distributed under the Apache License 2.0. The release archive includes the full license at `licenses/OpenAI-Codex-LICENSE.txt`. Codex remains a separate program and owns its authentication session, model access, updates, and usage limits.

## Satisfactory

Satisfactory, its game data, names, and assets belong to Coffee Stain Studios and their respective rights holders. Satisfactory Helper does not copy player saves, installed documentation dumps, or player-derived reference-world data into the repository. Source runs keep generated maps and snapshots under `.local-data`; bundled runs keep them under `%LOCALAPPDATA%\Satisfactory Helper`.

Satisfactory Helper is an unofficial community project. It is not affiliated with, sponsored by, or endorsed by Coffee Stain Studios.
