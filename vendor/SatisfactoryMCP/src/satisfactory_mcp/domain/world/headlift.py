"""Whether every fluid consumer stands low enough for its supply to reach it.

Head lift is an ABSOLUTE HEIGHT rather than a budget: a source at ``z`` pushes fluid to
``z + lift`` anywhere in a full pipe, so this propagates a maximum reachable altitude and a
pump is ``max(incoming, its own centre + its lift)`` and never a sum. A buffer is the one
element that BLOCKS an altitude: below ``BUFFER_TRANSMITS_ABOVE_FILL`` the line above it gets
only the buffer's own fill-proportional head. The finding is the CREST that stops a line,
named once with every consumer behind it, because that is where a pump would go. The rules,
the exclusions and what a reading does not mean are in `docs/fluids_model.md`.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from ...core.gamedata.constants import (
    BUFFER_TRANSMIT_BRACKET,
    BUFFER_TRANSMITS_ABOVE_FILL,
    MACHINE_HEAD_LIFT_M,
    MACHINE_MAX_HEAD_LIFT_M,
    PUMP_MEASURED_REACH_M,
)
from ...core.gamedata.model import GameData
from ...core.saveio import rows as saverows
from ...core.saveio.ports import PIPE as _PIPE
from ...core.saveio.ports import medium as _medium

__all__ = ["Crest", "HeadLift", "head_lift"]

#: The native classes this model has a rule for, matched on the NATIVE rather than the build
#: class: the T junction and the cross are one native, both buffer sizes are one native, and
#: a Valve is the same native as a pump -- which is why the pump rule reads its lift from
#: game data instead of its name, since a Valve's is zero.
_JUNCTION = "FGBuildablePipelineJunction"
_RESERVOIR = "FGBuildablePipeReservoir"
_PUMP = "FGBuildablePipelinePump"
#: One fluid volume each, so every port on one is the same node. A buffer is a body for
#: CONNECTION -- fluid crosses it at any fill -- and a barrier for HEIGHT until it is full.
_BODIES = (_JUNCTION, _RESERVOIR)

_CM_PER_M = 100.0
_LOW = -1e18


@dataclass(frozen=True)
class Crest:
    """One place a line climbs higher than the head behind it can push, and who is past it.

    ``head_m`` is the reachable altitude on the supply side and ``crest_m`` the altitude of
    the obstacle, both in world metres. ``marginal`` marks a crest cleared by the machines'
    tolerance ceiling but not by their rating: a warning rather than a fault.

    A fault's ``head_m`` is read from the TOLERANCE pass, the most generous of the two, so
    ``short_m`` is a lower bound on how far the line falls short.
    """

    fluid: str | None
    crest_m: float
    head_m: float
    consumers: tuple[str, ...]
    marginal: bool
    #: Whether the head behind this crest rests on the PINNED machine rating rather than on
    #: one the source's own class states in ``mDescription``. Six classes state theirs, so
    #: this is now false for most sources and means what it says.
    assumed: bool
    #: Where the crest stands, in world metres, or ``None`` when a device rather than a pipe
    #: is the obstacle.
    pos: tuple[float, float, float] | None
    #: Whether the head behind it is a part-full buffer's own surface. NOT a fault: the rig
    #: and the owner's base disagree here and the base wins on 48 saves, so this is "the line
    #: runs on what the buffer alone can give" rather than "these machines are cut off". See
    #: `docs/fluids_model.md`.
    buffer_gated: bool = False

    @property
    def short_m(self) -> float:
        return self.crest_m - self.head_m


@dataclass(frozen=True)
class HeadLift:
    """The head-lift verdict for a whole world.

    ``unfed`` consumers reach no source at all, and are deliberately not crests: that is a
    CONNECTION fault, which the manual's troubleshooting order puts before this one.
    """

    crests: tuple[Crest, ...]
    consumers: int
    #: One actor name per port in ``unfed``, so a diagnosis can ask about one machine. Named
    #: without a fluid because a network no source reaches has typically never carried one,
    #: and the save then records none for it.
    unfed_ports: tuple[str, ...]
    networks: int
    gas_networks: int
    #: Ports whose facing neither the save nor the building's nature settles. Each is taken
    #: BOTH ways, so a crest behind one is still reported and its head may be overstated.
    ambiguous_ports: int
    #: Buffers whose fill lands inside the bracket the transmission step was measured
    #: between, so ``BUFFER_TRANSMITS_ABOVE_FILL`` decided them and no measurement did.
    undecided_buffers: int = 0

    @property
    def unfed(self) -> int:
        return len(self.unfed_ports)

    @property
    def faults(self) -> tuple[Crest, ...]:
        """The crests this model will call a fault. A buffer-gated one is not one of them."""
        return tuple(c for c in self.crests if not c.buffer_gated)

    @property
    def buffer_lines(self) -> tuple[Crest, ...]:
        return tuple(c for c in self.crests if c.buffer_gated)


@dataclass
class _Plumbing:
    """The fluid graph as altitudes and crossings, with every gas network already dropped."""

    #: node -> altitude in world metres, averaged over the pipe ends that meet there.
    z: dict = field(default_factory=dict)
    #: node -> the fluid of the network it belongs to.
    fluid_of: dict = field(default_factory=dict)
    #: (u, v, the highest altitude between them, where that is).
    spans: list = field(default_factory=list)
    #: (inlet, outlet, rated lift, how high it reaches, powered). The reach is the measured
    #: one where a class has been measured and the declared ``mMaxPressure`` where it has not.
    devices: list = field(default_factory=list)
    #: (node, actor, the head lift its class STATES, or 0.0 where it states none).
    sources: list = field(default_factory=list)
    sinks: list = field(default_factory=list)
    #: (node, base altitude, height of the fluid standing in it, whether it passes head on).
    tanks: list = field(default_factory=list)
    networks: int = 0
    gas_networks: int = 0
    ambiguous: int = 0
    undecided_tanks: int = 0


class _Union:
    def __init__(self) -> None:
        self._parent: dict = {}

    def find(self, x):
        parent = self._parent
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _class_of(short: str) -> str:
    head, sep, _tail = short.rpartition("_C_")
    return head + "_C" if sep else short


def _couplings(projection: dict, native):
    """Union-find over ``(actor, role)``, which is what one fluid coupling joins."""
    graph = projection.get("graph") or {}
    actors = list(graph.get("actors") or ())
    roles = list(graph.get("roles") or ())
    fluid = {i for i, name in enumerate(roles) if _medium(name) == _PIPE}
    joins = _Union()
    ports: dict[int, set[int]] = defaultdict(set)
    for edge in graph.get("material") or ():
        if not isinstance(edge, (list, tuple)) or len(edge) < 4:
            continue
        a, b, ra, rb = edge[0], edge[1], edge[2], edge[3]
        if ra not in fluid or rb not in fluid:
            continue
        joins.union((a, ra), (b, rb))
        ports[a].add(ra)
        ports[b].add(rb)
    for actor, held in ports.items():
        if 0 <= actor < len(actors) and native(actors[actor]) in _BODIES:
            first = min(held)
            for role in held:
                joins.union((actor, first), (actor, role))
    return joins, ports, actors, roles


def _spans(projection: dict, joins, ports, gas: set, plumbing: _Plumbing) -> dict:
    """Fill in node altitudes and pipe crossings; hand back each node's network."""
    graph = projection.get("graph") or {}
    roles = list(graph.get("roles") or ())
    role_ix = {name: i for i, name in enumerate(roles)}
    c0, c1 = role_ix.get("PipelineConnection0"), role_ix.get("PipelineConnection1")
    heights: dict = defaultdict(list)
    network_of: dict = {}
    for seg in saverows.iter_pipe_segments(projection):
        if seg.actor_index < 0 or seg.network_index in gas:
            continue
        held = ports.get(seg.actor_index, ())
        ends = [
            (joins.find((seg.actor_index, role)), point)
            for role, point in ((c0, seg.points[0]), (c1, seg.points[-1]))
            if role in held
        ]
        for node, point in ends:
            heights[node].append(point[2] / _CM_PER_M)
            network_of.setdefault(node, seg.network_index)
        if len(ends) == 2 and ends[0][0] != ends[1][0]:
            top = max(seg.points, key=lambda p: p[2])
            plumbing.spans.append(
                (
                    ends[0][0],
                    ends[1][0],
                    top[2] / _CM_PER_M,
                    tuple(v / _CM_PER_M for v in top[:3]),
                )
            )
    plumbing.z.update({node: sum(v) / len(v) for node, v in heights.items()})
    return network_of


def _build(projection: dict, game: GameData, powered: set[str]) -> _Plumbing:
    """The plumbing as a height graph. Gas is dropped here and never reaches the model."""

    def native(actor: str) -> str:
        building = game.buildings.get(_class_of(actor))
        return building.native if building else ""

    joins, ports, actors, roles = _couplings(projection, native)
    role_ix = {name: i for i, name in enumerate(roles)}
    d0, d1 = role_ix.get("Connection0"), role_ix.get("Connection1")

    fluid_of = {
        i: row.get("fluid")
        for i, row in enumerate((projection.get("pipes") or {}).get("networks") or ())
        if isinstance(row, dict)
    }
    gas = {
        i
        for i, name in fluid_of.items()
        if name in game.items and game.items[name].form == "RF_GAS"
    }

    out = _Plumbing(networks=len(fluid_of) - len(gas), gas_networks=len(gas))
    network_of = _spans(projection, joins, ports, gas, out)
    out.fluid_of = {node: fluid_of.get(ix) for node, ix in network_of.items()}

    producers = {r.get("cls") for r in projection.get("extractors") or () if isinstance(r, dict)}
    consumers = {r.get("cls") for r in projection.get("generators") or () if isinstance(r, dict)}
    tank_rows = {
        str(r.get("instance", "")).rsplit(".", 1)[-1]: r
        for r in projection.get("storage") or ()
        if isinstance(r, dict)
    }

    for actor, held in ports.items():
        if not (0 <= actor < len(actors)):
            continue
        name = actors[actor]
        cls = _class_of(name)
        kind = native(name)
        if kind == _PUMP:
            building = game.buildings[cls]
            if d0 in held and d1 in held:
                inlet, outlet = joins.find((actor, d0)), joins.find((actor, d1))
                if inlet in out.z and outlet in out.z:
                    out.devices.append(
                        (
                            inlet,
                            outlet,
                            building.head_lift_m,
                            PUMP_MEASURED_REACH_M.get(cls, building.max_head_lift_m),
                            name in powered,
                        )
                    )
            continue
        if kind == _RESERVOIR:
            _add_tank(out, game, cls, tank_rows.get(name), joins.find((actor, min(held))))
            continue
        if kind == _JUNCTION:
            continue
        stated = getattr(game.buildings.get(cls), "machine_head_lift_m", 0.0)
        for role in held:
            spelled = roles[role] if 0 <= role < len(roles) else ""
            if spelled.startswith("Pipeline"):
                continue  # a pipe piece, which is a span rather than a port
            port = joins.find((actor, role))
            if port not in out.z:
                continue  # every pipe it touches was on a gas network
            # Most local evidence first: the port's own typing outranks the building's
            # nature, so an extractor that also takes a fluid in is read a port at a time.
            if spelled.startswith("PipeOutputFactory"):
                out.sources.append((port, name, stated))
            elif spelled.startswith("PipeInputFactory"):
                out.sinks.append((port, name))
            elif cls in producers:
                out.sources.append((port, name, stated))
            elif cls in consumers:
                out.sinks.append((port, name))
            else:
                # Nothing says which way it faces, so it is taken as both: a source that may
                # not be one, and a consumer that may not be one.
                out.ambiguous += 1
                out.sources.append((port, name, stated))
                out.sinks.append((port, name))
    return out


def _add_tank(out: _Plumbing, game: GameData, cls: str, row, node) -> None:
    building = game.buildings.get(cls)
    if node not in out.z or row is None or building is None or not building.footprint:
        return
    if not building.storage_capacity_m3:
        return
    fill = float(row.get("stored_m3") or 0.0) / building.storage_capacity_m3
    base = float((row.get("pos") or (0, 0, 0))[2]) / _CM_PER_M
    low, high = BUFFER_TRANSMIT_BRACKET
    if low <= fill < high:
        out.undecided_tanks += 1
    out.tanks.append(
        (
            node,
            base,
            building.footprint.height_m * min(1.0, fill),
            fill >= BUFFER_TRANSMITS_ABOVE_FILL,
        )
    )


def _adjacency(plumbing: _Plumbing):
    spans: dict = defaultdict(list)
    for u, v, crest, _pos in plumbing.spans:
        spans[u].append((v, crest))
        spans[v].append((u, crest))
    devices: dict = defaultdict(list)
    for inlet, outlet, rated, ceiling, powered in plumbing.devices:
        devices[inlet].append((outlet, rated, ceiling, powered))
    return spans, devices


def _fed(plumbing: _Plumbing) -> set:
    """Every node fluid arrives at with heights ignored: rung (1) of the manual's ladder."""
    spans, devices = _adjacency(plumbing)
    seen = {node for node, *_rest in plumbing.sources} | {n for n, *_rest in plumbing.tanks}
    stack = list(seen)
    while stack:
        node = stack.pop()
        for peer, _crest in spans[node]:
            if peer not in seen:
                seen.add(peer)
                stack.append(peer)
        for outlet, *_rest in devices[node]:
            if outlet not in seen:
                seen.add(outlet)
                stack.append(outlet)
    return seen


def _spread(
    plumbing: _Plumbing, fed: set, machine_lift: float, ceiling: bool
) -> tuple[dict, dict, dict]:
    """Reachable altitude per node, and the two provenances a crest has to declare.

    A relaxation rather than one pass: loops and several sources per network are the normal
    shape of a fluid system, and a node's answer can improve after it has been visited.
    """
    z = plumbing.z
    reach: dict = {}
    assumed: dict = {}
    gated: dict = {}
    #: A buffer too empty to pass head on caps the altitude AT its node rather than only what
    #: it emits, so the crest search reads the same height the line above it actually gets.
    capped = {
        node: base + column for node, base, column, transmits in plumbing.tanks if not transmits
    }

    def centre(inlet, outlet) -> float:
        return (z.get(inlet, 0.0) + z.get(outlet, 0.0)) / 2.0

    def raise_to(node, height: float, from_machine: bool, from_buffer: bool) -> bool:
        own = capped.get(node)
        if own is not None and own < height:
            height, from_machine, from_buffer = own, False, True
        if height <= reach.get(node, _LOW):
            return False
        # A buffer stands in what it holds, so its surface settles its own node even when
        # that surface is below its connectors -- which is what makes the barrier a CREST
        # the report can name rather than a node that quietly never arrives.
        if height < z.get(node, _LOW) and own is None:
            return False
        reach[node] = height
        assumed[node] = from_machine
        gated[node] = from_buffer
        return True

    for node, _actor, stated in plumbing.sources:
        # The class's own description outranks the pinned figure. The ceiling is measured
        # on one class and inherited, so it is never taken below a stated rating.
        lift = max(stated, machine_lift) if ceiling else (stated or machine_lift)
        raise_to(node, z.get(node, 0.0) + lift, not stated, False)
    for node, base, column, transmits in plumbing.tanks:
        raise_to(node, base + column, False, not transmits)
    for inlet, outlet, rated, tolerance, powered in plumbing.devices:
        # A POWERED PUMP DRAWS. Its inlet is settled by rung (1) -- fluid arrives there --
        # and not by the altitude test every other node keeps, so it lifts from its own
        # centre however far below the surface behind it stands.
        if powered and tolerance and inlet in fed:
            drawn = centre(inlet, outlet) + (tolerance if ceiling else rated)
            raise_to(outlet, drawn, False, False)

    spans, devices = _adjacency(plumbing)
    work = deque(reach)
    while work:
        node = work.popleft()
        height, machine, buffered = reach[node], assumed[node], gated[node]
        for peer, crest in spans[node]:
            if height >= crest and raise_to(peer, height, machine, buffered):
                work.append(peer)
        for outlet, rated, tolerance, powered in devices[node]:
            behind = buffered
            if not tolerance:
                lifted, from_machine = height, machine  # a valve, which lifts nothing
            elif powered:
                lifted = max(height, centre(node, outlet) + (tolerance if ceiling else rated))
                from_machine = machine and lifted == height
                behind = buffered and lifted == height
            else:
                # A pump no wire reaches passes fluid and sets the head past it to its own
                # centre, so everything the line had climbed on the way in is gone.
                lifted, from_machine = centre(node, outlet), False
                behind = False
            if raise_to(outlet, lifted, from_machine, behind):
                work.append(outlet)
    return reach, assumed, gated


def _walls(plumbing: _Plumbing, reach: dict):
    """Every crossing the head stops at, cheapest to clear first."""
    out = []
    for a, b, crest, pos in plumbing.spans:
        for u, v in ((a, b), (b, a)):
            obstacle = max(crest, plumbing.z.get(v, _LOW))
            if u in reach and v not in reach and reach[u] < obstacle:
                out.append((u, v, obstacle, pos))
    for inlet, outlet, *_rest in plumbing.devices:
        if inlet in reach and outlet not in reach:
            out.append((inlet, outlet, plumbing.z.get(outlet, 0.0), None))
    out.sort(key=lambda w: w[2] - reach[w[0]])
    return out


def _crests(
    plumbing: _Plumbing,
    reach: dict,
    assumed: dict,
    cut_off: set,
    marginal: bool,
    gated: dict | None = None,
):
    """Group cut-off consumers under the crest that is cheapest to clear.

    A consumer can sit behind several crests; it is named once, under the lowest of them,
    because clearing that one is what the player would do first.
    """
    beyond: dict = defaultdict(list)
    for u, v, _crest, _pos in plumbing.spans:
        for near, far in ((u, v), (v, u)):
            if far not in reach:
                beyond[near].append(far)
    for inlet, outlet, *_rest in plumbing.devices:
        if outlet not in reach:
            beyond[inlet].append(outlet)

    consumers_at: dict = defaultdict(list)
    for node, actor in plumbing.sinks:
        if node in cut_off:
            consumers_at[node].append(actor)

    claimed: set = set()
    out: list[Crest] = []
    for u, v, obstacle, pos in _walls(plumbing, reach):
        seen, stack, found = {v}, [v], []
        while stack:
            node = stack.pop()
            found.extend(a for a in consumers_at.get(node, ()) if a not in claimed)
            for peer in beyond[node]:
                if peer not in seen:
                    seen.add(peer)
                    stack.append(peer)
        if not found:
            continue
        claimed.update(found)
        out.append(
            Crest(
                fluid=plumbing.fluid_of.get(u),
                crest_m=obstacle,
                head_m=reach[u],
                consumers=tuple(sorted(found)),
                marginal=marginal,
                assumed=bool(assumed.get(u)),
                pos=pos,
                buffer_gated=bool((gated or {}).get(u)),
            )
        )
    return out


def head_lift(projection: dict, game: GameData, graph) -> HeadLift:
    """Every fluid consumer the model cannot lift supply to, grouped by the crest in the way.

    ``graph`` is the world's ``FactoryGraph``, which is where a pump's power comes from --
    the save states it nowhere else. A consumer that reaches no source at all is counted in
    ``unfed`` and never becomes a crest.
    """
    powered = {name for name in graph.cls if graph.neighbours(name, "power")}
    plumbing = _build(projection, game, powered)

    fed = _fed(plumbing)
    rated, rated_from, rated_gate = _spread(plumbing, fed, MACHINE_HEAD_LIFT_M, False)
    ceiling, ceiling_from, ceiling_gate = _spread(plumbing, fed, MACHINE_MAX_HEAD_LIFT_M, True)

    supplied = {node for node, _actor in plumbing.sinks if node in fed}
    crests = _crests(
        plumbing, ceiling, ceiling_from, supplied - set(ceiling), False, ceiling_gate
    )
    crests += _crests(
        plumbing, rated, rated_from, (supplied & set(ceiling)) - set(rated), True, rated_gate
    )
    crests.sort(key=lambda c: (c.buffer_gated, c.marginal, -c.short_m))
    return HeadLift(
        crests=tuple(crests),
        consumers=len(plumbing.sinks),
        unfed_ports=tuple(sorted(a for node, a in plumbing.sinks if node not in fed)),
        networks=plumbing.networks,
        gas_networks=plumbing.gas_networks,
        ambiguous_ports=plumbing.ambiguous,
        undecided_buffers=plumbing.undecided_tanks,
    )
