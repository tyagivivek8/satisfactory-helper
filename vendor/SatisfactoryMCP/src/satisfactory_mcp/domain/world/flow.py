"""Which way the fluid goes, inferred from the plumbing rather than read off a pipe.

The save stores no flow direction, but it does store the couplings and it types the ports:
``PipeInputFactory`` is a consumer, ``PipeOutputFactory`` a producer, ``ConnectionAny0``/``1``
an explicit "either way", and ``PipelineConnection0``/``1`` are a segment's first and last
spline point, which is what ``forward`` and ``reverse`` are measured against. Two models then
orient pipes -- the cut, where an edge whose removal splits the network carries everything
from the side holding a producer and no consumer to a side holding a consumer, and the one-way
device, a pump or valve running ``Connection0`` to ``Connection1`` -- and conservation
propagates both to a fixpoint. Both decline wherever more than one ordering is consistent: an
edge inside a cycle splits nothing, and a trunk with producers and consumers on both sides has
no fixed direction without the RATES. On the reference save 365 of 503 pipes resolve.
"""

from __future__ import annotations

from collections import defaultdict

from ...core.saveio import rows as saverows
from ...core.saveio.ports import PIPE as _PIPE
from ...core.saveio.ports import medium as _medium

__all__ = ["FORWARD", "REVERSE", "UNKNOWN", "pipe_flow"]

#: Along the segment's own point order, against it, and "we will not say".
FORWARD = "forward"
REVERSE = "reverse"
UNKNOWN = "unknown"

#: A junction and a buffer are ONE volume of fluid: what arrives at any port can leave by any
#: other, so their ports collapse into a single node. The T and the cross are both here and
#: both have to be: a junction left out is a CUT in the network, not a missing node.
_JUNCTIONS = (
    "Build_PipelineJunction_Cross_C",
    "Build_PipelineJunction_T_C",
)
#: The bodies that also HOLD fluid, which is what ``_solve``'s guard needs and a junction is
#: not: a tank can accept flow that nothing beyond it consumes, so it can end a route.
_STORES = (
    "Build_IndustrialTank_C",
    "Build_PipeStorageTank_C",
)
_BODIES = _JUNCTIONS + _STORES

#: One-way by construction, from ``Connection0`` (the inlet) to ``Connection1``.
_ONE_WAY = (
    "Build_PipelinePump_C",
    "Build_PipelinePumpMk2_C",
    "Build_Valve_C",
)

#: Basis labels, most local evidence first.
BASIS_PORT = "machine port"
BASIS_DEVICE = "pump"
BASIS_NETWORK = "propagated"
BASIS_NONE = "unresolved"


def _class_of(short: str) -> str:
    """``Build_OilRefinery_C_2147245036`` -> ``Build_OilRefinery_C``."""
    head, sep, _tail = short.rpartition("_C_")
    return head + "_C" if sep else short


class _Union:
    """Union-find over ``(actorIndex, roleIndex)``, which is what a coupling joins."""

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x: tuple[int, int]) -> tuple[int, int]:
        parent = self._parent
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _build(projection: dict) -> tuple[list, list, list, dict, set]:
    """The plumbing as (pipe edges, one-way edges, terminals, adjacency, buffer nodes).

    A node is a place fluid can be: a coupling between two connectors, or a junction or
    buffer body whose ports have been merged into one. The buffer nodes come out separately
    because a tank holds fluid, so it can supply and it can accept, yet it PRODUCES nothing
    and CONSUMES nothing and so can never orient a pipe.
    """
    graph = projection.get("graph") or {}
    actors = list(graph.get("actors") or ())
    roles = list(graph.get("roles") or ())
    fluid_role = {i for i, name in enumerate(roles) if _medium(name) == _PIPE}
    role_name = {i: name for i, name in enumerate(roles)}

    joins = _Union()
    ports_of: dict[int, set[int]] = defaultdict(set)
    for edge in graph.get("material") or ():
        if not isinstance(edge, (list, tuple)) or len(edge) < 4:
            continue
        a, b, ra, rb = edge[0], edge[1], edge[2], edge[3]
        if ra not in fluid_role or rb not in fluid_role:
            continue
        joins.union((a, ra), (b, rb))
        ports_of[a].add(ra)
        ports_of[b].add(rb)

    for actor, ports in ports_of.items():
        if 0 <= actor < len(actors) and _class_of(actors[actor]) in _BODIES:
            first = min(ports)
            for role in ports:
                joins.union((actor, first), (actor, role))

    segments = list(saverows.iter_pipe_segments(projection))
    pipe_actors = {seg.actor_index for seg in segments if seg.actor_index >= 0}

    # An extractor cannot consume what it pulls out of the ground and a generator cannot
    # produce its fuel, which settles the buildings whose single port carries the generic
    # ``FGPipeConnectionFactory`` name.
    producers = {r.get("cls") for r in projection.get("extractors") or () if isinstance(r, dict)}
    consumers = {r.get("cls") for r in projection.get("generators") or () if isinstance(r, dict)}

    devices: list[tuple[tuple, tuple]] = []
    terminals: list[tuple[tuple, str, int]] = []

    role_ix = {name: i for i, name in enumerate(roles)}
    stores: set = set()
    for actor, ports in ports_of.items():
        cls = _class_of(actors[actor]) if 0 <= actor < len(actors) else ""
        if actor in pipe_actors:
            continue  # emitted below, in the segments' own order
        if cls in _BODIES:
            if cls in _STORES:
                stores.add(joins.find((actor, min(ports))))
            continue
        if cls in _ONE_WAY:
            c0, c1 = role_ix.get("Connection0"), role_ix.get("Connection1")
            if c0 in ports and c1 in ports:
                devices.append((joins.find((actor, c0)), joins.find((actor, c1))))
            continue
        for role in ports:
            name = role_name.get(role, "")
            if name.startswith("PipeInputFactory"):
                kind = "sink"
            elif name.startswith("PipeOutputFactory"):
                kind = "source"
            elif name.startswith("ConnectionAny"):
                kind = "any"
            elif cls in producers:
                kind = "source"
            elif cls in consumers:
                kind = "sink"
            else:
                kind = "any"
            if kind != "any":
                terminals.append((joins.find((actor, role)), kind, actor))

    # One entry per ROW of the table, not per row that decoded: ``/api/pipes`` joins to this
    # list by a segment's position, so a torn row owes it a slot that says "no idea" rather
    # than shifting every pipe after it up by one.
    c0, c1 = role_ix.get("PipelineConnection0"), role_ix.get("PipelineConnection1")
    pipes: list[tuple[tuple | None, tuple | None]] = [(None, None)] * saverows.pipe_segment_count(
        projection
    )
    for seg in segments:
        actor = seg.actor_index
        ports = ports_of.get(actor, ()) if actor >= 0 else ()
        pipes[seg.index] = (
            joins.find((actor, c0)) if c0 in ports else None,
            joins.find((actor, c1)) if c1 in ports else None,
        )

    adjacency: dict[tuple, list] = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        adjacency[n0].append((n1, ("pipe", i)))
        adjacency[n1].append((n0, ("pipe", i)))
    for j, (n0, n1) in enumerate(devices):
        adjacency[n0].append((n1, ("device", j)))
        adjacency[n1].append((n0, ("device", j)))
    return pipes, devices, terminals, adjacency, stores


def _reach(adjacency: dict, start, without) -> set:
    """Everything fluid could get to from ``start`` without using edge ``without``."""
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for peer, tag in adjacency.get(node, ()):
            if tag == without or peer in seen:
                continue
            seen.add(peer)
            stack.append(peer)
    return seen


def _solve(
    pipes,
    devices,
    terminals,
    adjacency,
    stores=frozenset(),
    *,
    drop_actor=None,
    cuts=True,
    one_way=True,
):
    """Direction per pipe as +1 (points[0] to points[-1]), -1 (the reverse) or 0."""
    source: dict = defaultdict(int)
    sink: dict = defaultdict(int)
    for node, kind, actor in terminals:
        if actor == drop_actor:
            continue
        (source if kind == "source" else sink)[node] += 1
    total_source, total_sink = sum(source.values()), sum(sink.values())

    settled = [0] * len(pipes)
    if cuts:
        for i, (n0, n1) in enumerate(pipes):
            if n0 is None or n1 is None or n0 == n1:
                continue
            near = _reach(adjacency, n0, ("pipe", i))
            if n1 in near:
                continue  # a cycle: both orderings are consistent, so neither is claimed
            near_source = sum(source[n] for n in near)
            near_sink = sum(sink[n] for n in near)
            if near_source and not near_sink and total_sink - near_sink:
                settled[i] = 1
            elif near_sink and not near_source and total_source - near_source:
                settled[i] = -1

    if not one_way:
        devices = ()

    # Conservation, to a fixpoint. At a node that is nothing but plumbing, what arrives has
    # to leave, so a single unsettled edge among same-facing settled ones is forced.
    incident: dict[tuple, list] = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        incident[n0].append(("pipe", i, True))
        incident[n1].append(("pipe", i, False))
    for j, (n0, n1) in enumerate(devices):
        incident[n0].append(("device", j, True))
        incident[n1].append(("device", j, False))
    inlets = {n0 for n0, _n1 in devices}
    outlets = {n1 for _n0, n1 in devices}

    changed = True
    while changed:
        changed = False
        for node, rows in incident.items():
            if source[node] or sink[node]:
                continue  # it has a port of its own, so nothing here is forced
            arriving = leaving = 0
            open_edge = None
            for kind, index, at_first in rows:
                if kind == "device":
                    # The device draws fluid out of its inlet node and into its outlet node.
                    leaving += 1 if at_first else 0
                    arriving += 0 if at_first else 1
                elif settled[index] == 0:
                    if open_edge is not None:
                        open_edge = False
                        break
                    open_edge = (index, at_first)
                elif (settled[index] == 1) == at_first:
                    leaving += 1
                else:
                    arriving += 1
            if not open_edge:
                continue
            index, at_first = open_edge
            if arriving and not leaving:
                direction = 1 if at_first else -1
            elif leaving and not arriving:
                direction = -1 if at_first else 1
            else:
                continue
            # The guard. Settling this edge says fluid crosses it, so the side it would be
            # sent to has to hold something able to take it: a machine port, a pump's intake
            # (it pulls at its inlet and pushes at its outlet) or a tank. Without this, flow
            # is invented into a bare stub of pipe that ends in nothing.
            n0, n1 = pipes[index]
            far = n1 if at_first else n0
            outbound = (direction == 1) == at_first
            wanted, wanted_ports = (sink, inlets) if outbound else (source, outlets)
            beyond = _reach(adjacency, far, ("pipe", index))
            if (
                not any(wanted[n] for n in beyond)
                and not (beyond & wanted_ports)
                and not (beyond & stores)
            ):
                continue
            settled[index] = direction
            changed = True
    return settled


def pipe_flow(projection: dict) -> list[dict]:
    """One ``{"direction", "basis"}`` per pipe segment, in the segments' own order.

    ``direction`` is ``forward`` along the segment's stored points, ``reverse`` against them,
    or ``unknown``. ``basis`` names the evidence, from the most local outwards: a typed
    ``machine port`` at one end of this very pipe, a one-way ``pump`` at one end of it, or
    ``propagated`` when only the shape of the wider network settles it.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    settled = _solve(pipes, devices, terminals, adjacency, stores)

    port_nodes = {node for node, _kind, _actor in terminals}
    device_nodes = {node for edge in devices for node in edge}
    out = []
    for i, (n0, n1) in enumerate(pipes):
        if not settled[i]:
            out.append({"direction": UNKNOWN, "basis": BASIS_NONE})
            continue
        if n0 in port_nodes or n1 in port_nodes:
            basis = BASIS_PORT
        elif n0 in device_nodes or n1 in device_nodes:
            basis = BASIS_DEVICE
        else:
            basis = BASIS_NETWORK
        out.append({"direction": FORWARD if settled[i] == 1 else REVERSE, "basis": basis})
    return out
