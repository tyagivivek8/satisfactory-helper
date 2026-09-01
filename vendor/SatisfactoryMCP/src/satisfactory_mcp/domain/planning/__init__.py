"""Turning a wish into a buildable plan: solve, slice, lay out, cost and compare.

Import-free on purpose, like the other domain packages. Several modules here reach
into ``domain.world`` and ``domain.factories``, while ``domain.world.state`` lazily
reaches back for ``PlanStore``; a re-exporting ``__init__`` would turn those one-way
edges into an import cycle.

Nothing in this package formats a response. The renderers that used to live in
``bom``, ``byproducts``, ``compare`` and ``diff`` are in ``presenters.text``.
"""
