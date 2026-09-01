"""The offline generators, and the one module they share.

A package marker rather than a package: nothing is exported here, and nothing in ``src/``
may import anything under ``tools/``. It exists so ``tools/_common.py`` can be imported by
name -- ``from tools._common import ...`` -- from a generator that is run as a script, so
that the directory has one copy of the install path, one copy of the ``--game`` argument,
and one place that says how to install what a generator needs.

The test suite reaches the generators the same way -- ``from tools import gen_map_image``
-- to hold each one against the server that reads what it writes. That is what
``tests/conftest.py`` puts the repository root on ``sys.path`` for, and it is why a
generator must stay importable without doing anything: everything a run does happens in
``main``, never at module scope.

The generators are not shipped: ``pyproject.toml`` packages ``src/satisfactory_mcp`` and
``src/pioneersav`` and nothing else, so this package is present in a checkout and absent
from every wheel, which is the correct shape for a tool that reads the reader's own
installed copy of a game.
"""
