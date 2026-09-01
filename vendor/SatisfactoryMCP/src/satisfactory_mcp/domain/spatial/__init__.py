"""Where things are: geometry, resource nodes, regions, elevation, ranking.

Deliberately import-free, as the old top-level ``spatial`` package was. The
modules here are leaves -- ``geo`` in particular is imported by ``factories``,
``world`` and ``collectibles`` -- so re-exporting from this ``__init__`` would
drag the whole package in on any one of those edges.
"""
