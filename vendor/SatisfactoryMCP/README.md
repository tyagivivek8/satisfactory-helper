# Vendored SatisfactoryMCP runtime

This directory contains a trimmed, modified runtime subset of
[SatisfactoryMCP](https://github.com/lukszi/SatisfactoryMCP) pinned to commit
`ade73e6c4736937eb49cc54364def7d6b30873d6`.

Included here:

- `src/`: the save parser and read-only planning engine used by Satisfactory Helper;
- `tools/`: generators that read each player's installed game and private save snapshot;
- `pyproject.toml`: the dependency and package metadata needed to install the runtime; and
- `LICENSE`: the upstream PolyForm Noncommercial 1.0.0 terms and Required Notice.

Upstream development notes, tests, media, and player-derived reference-world fixtures are
not redistributed with Satisfactory Helper. Runtime node, collectible, region, and map data
is generated locally and remains outside Git.

Satisfactory Helper's modifications and attribution are documented in the repository's
[third-party notices](../../THIRD_PARTY_NOTICES.md).

Required Notice: Copyright Lukas Szimtenings (https://github.com/lukszi/SatisfactoryMCP)
