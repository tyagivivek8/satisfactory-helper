"""The save as a whole: who it is, what it holds, what stands on the map.

``state.WorldState`` is the aggregate every other domain package takes as an
argument. It owns no subject of its own any more -- each facet in this package
and in ``progression``, ``power`` and ``collectibles`` owns exactly one, and the
aggregate holds them and delegates.

Deliberately import-free: ``progression`` reaches in here for ``Inventory`` and
``state`` reaches out to ``progression``, so a re-exporting ``__init__`` would
turn two one-way edges into an import cycle.
"""
