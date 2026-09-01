"""Turning what a player typed into a named machine set.

Lives with the graph rather than with any one tool group because more than one caller
needs it: the factory tools, ``diff_vs_save`` and ``plan_layout`` all start here.
"""

from __future__ import annotations

from . import select as gsel


def resolve_factory(st, factory: str):
    """A label name, a selector, or a proposal index -- in that order.

    Label first because that is what a player types. Falling through to the selector
    grammar means ``factory_query("proposal:3", ...)`` works before anything is named.
    """
    label = st.labels.find(factory)
    if label is not None:
        alive = set(st.graph.machines())
        return label.name, [m for m in label.anchors if m in alive]
    try:
        picked = gsel.select_machines(
            [factory],
            st.graph,
            st.game,
            st.projection,
            st.labels,
            structures=st.structures,
            proposals=st.proposals,
        )
    except gsel.SelectorError as exc:
        known = ", ".join(x.name for x in st.labels.labels) or "(none named yet)"
        raise gsel.SelectorError(f"{exc}. Named factories: {known}") from exc
    return factory, picked
