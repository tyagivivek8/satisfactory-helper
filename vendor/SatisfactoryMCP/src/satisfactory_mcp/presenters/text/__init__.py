"""The compact TSV presenter: primitives, plus one formatter module per concept.

Context budget is the binding constraint on every response here -- see
``primitives`` for the rules that follow from it.

Deliberately empty of imports, and it has to stay that way. Importing a submodule
runs this file first, so re-exporting the formatters here would drag the whole
planning package in behind every ``primitives.num`` call -- and the tool modules
reach for ``primitives`` on every response.

``render.py`` is gone, and so is every import of it. There is one spelling now and it is
the same one everywhere: ``primitives`` imported under the local alias ``render``, in the
eight MCP tool modules, in the nine formatter modules beside this one, and in the two test
modules that format anything -- which is the mapping ``tests/test_architecture.py`` pins.
The alias is kept because ``render.table`` reads better at a call site than
``primitives.table`` does; what was removed is the second MODULE, not the second name.
"""
