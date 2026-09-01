"""One coherence score over every signal, agglomerated into proposed factories.

Each signal fails alone -- power islands over-merge, belt components fragment, slabs are
blind to ground-built machines -- while together they recover ten of the reference save's
twelve hand-named factories exactly, at precision 1.000 and recall 0.945 under
leave-one-factory-out. Precision holds on every fold, so this never merges two factories
and only ever splits one. The weights are not what does the work: perturbing every one of
them by 50% costs 0.02 F1, while the complete-linkage rule and the span cap carry the
result outright. All of it is fitted against one save and one player's building style, so
that insensitivity to the weights is the only evidence there is that it generalises.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..spatial import geo
from .model import FactoryGraph
from .structure import Structures

__all__ = [
    "MAX_DEPENDENT_RECIPES",
    "MAX_SPAN_M",
    "MIN_EXCLUSIVITY",
    "NEAREST_MARGIN",
    "WEIGHTS",
    "Proposal",
    "propose",
]

#: Signal weights, round because they are not load-bearing. They are kept so the evidence
#: report can name which signals fired, not because the arithmetic needs them.
WEIGHTS = {
    "slab": 10.0,  # same foundation platform
    "near": 5.0,  # within NEAR_M of each other
    "prod": 4.0,  # makes the same thing
    "belt": 3.0,  # same belt/pipe component
    "supply": 3.0,  # one's output is the other's input
}

#: Evidence has to beat this for two clusters to merge. Its scale is arbitrary in the same
#: way the weights are; it is the sign that matters.
PRIOR = 1.0

#: Proximity threshold for the ``near`` signal, in metres.
NEAR_M = 100.0

#: A dependent is absorbed when this share of everything it reaches over belts and pipes
#: lies in one other cluster. A water-pump farm scores 94%: 16 of the 17 machines its
#: pipes reach are the coal generators it exists to feed.
MIN_EXCLUSIVITY = 0.8

#: ...but only if it is at most this fraction of the cluster absorbing it. Without this,
#: precision falls from 1.000 to 0.709 as large factories that mostly feed each other get
#: welded together.
MAX_DEPENDENT_RATIO = 0.5

#: ...and only if it MANUFACTURES almost nothing. Infrastructure -- miners, water pumps,
#: generators -- runs no recipe at all, so a cluster with several machines actually making
#: something is a factory in its own right however exclusively it feeds another. Size
#: alone cannot express this: a small area with three manufacturers and a dozen burners
#: powering them sits comfortably inside the size ratio and is still its own factory.
MAX_DEPENDENT_RECIPES = 2

#: A dependent is also absorbed when the cluster its belts reach FIRST is this many times
#: nearer, in material hops, than the runner-up. Exclusivity alone cannot attribute a
#: remote mine, because the two factories it might belong to are belt-connected to EACH
#: OTHER downstream, which dilutes the share reaching either; first arrival is unambiguous.
NEAREST_MARGIN = 2.0

#: No proposal may span more than this. THE load-bearing constant -- removing it drops
#: precision from 1.000 to 0.776. It also caps a proposal's diameter, so a genuinely
#: sprawling factory (the reference oil setup spans 381 m) is proposed in pieces.
MAX_SPAN_M = 250.0


@dataclass
class Proposal:
    """A proposed factory and the evidence that produced it."""

    machines: list[str]
    #: Weakest internal link. Under complete linkage every pair scores at least this.
    #: 0.0 for a one-machine proposal, which has no internal link to be weakest.
    cohesion: float = 0.0
    evidence: Counter = field(default_factory=Counter)
    seeded_by: str = ""
    #: Sizes of the pieces this was assembled from, largest first. More than one entry
    #: means dependents were absorbed -- "47 = 32 + 6 + 6 + 2 + 1" is a coal plant that
    #: reclaimed its water pumps and its miners.
    parts: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.machines)


def _feature_fn(
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    structures: Structures,
):
    """Build the per-pair feature extractor once, with every lookup pre-indexed."""
    pos: dict[str, tuple[float, float, float]] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = tuple(record["pos"])

    recipe_of = {
        r["instance"].rsplit(".", 1)[-1]: r["recipe"]
        for r in projection.get("machines", ())
        if r.get("recipe")
    }
    products: dict[str, frozenset[str]] = {}
    ingredients: dict[str, frozenset[str]] = {}
    for machine, rid in recipe_of.items():
        recipe = game.recipes.get(rid)
        if recipe is None:
            continue
        products[machine] = frozenset(game.item_name(f.item) for f in recipe.products)
        ingredients[machine] = frozenset(game.item_name(f.item) for f in recipe.ingredients)

    component = {m: k for k, comp in enumerate(graph.machine_components("material")) for m in comp}
    slab = structures.slab_of

    def features(a: str, b: str) -> tuple[dict[str, float], float]:
        pa, pb = pos.get(a), pos.get(b)
        distance_m = geo.distance_m(pa[:2], pb[:2]) if pa and pb else math.inf
        sa, sb = slab.get(a), slab.get(b)
        prod_a, prod_b = products.get(a), products.get(b)
        feats = {
            "slab": float(sa is not None and sa == sb),
            "near": float(distance_m <= NEAR_M),
            "prod": float(bool(prod_a) and prod_a == prod_b),
            "belt": float(component.get(a, -1) == component.get(b, -2)),
            "supply": float(
                bool(prod_a and ingredients.get(b) and prod_a & ingredients[b])
                or bool(prod_b and ingredients.get(a) and prod_b & ingredients[a])
            ),
        }
        return feats, distance_m

    return features


def attach_dependents(
    clusters: list[list[str]],
    graph: FactoryGraph,
    manufacturing: set[str] | None = None,
    min_exclusivity: float = MIN_EXCLUSIVITY,
    max_ratio: float = MAX_DEPENDENT_RATIO,
    max_recipes: int = MAX_DEPENDENT_RECIPES,
    nearest_margin: float = NEAREST_MARGIN,
    rounds: int = 3,
) -> list[list[str]]:
    """Absorb clusters whose entire material existence serves one other cluster.

    Complete linkage cannot express this: a coal plant fed by two separate pipe networks
    scores every pump against a generator in the OTHER network negative, and linkage takes
    the minimum over cross pairs, so one blind pair vetoes a merge that 94% of the pumps'
    reach argues for. Exclusivity is a property of a cluster rather than of a pair, so it
    cannot be a feature and has to be this second pass. It is asymmetric: a pump farm
    belongs to the plant it feeds, and a plant does not belong to its pumps.

    A candidate qualifies either by exclusivity -- most of what it reaches is one cluster,
    which fits something embedded in the factory it serves -- or by nearest consumer,
    which fits something at the far end of a long belt. ``manufacturing`` is the set of
    machines running a recipe, and a candidate with more than ``max_recipes`` of them is
    left alone whatever its exclusivity or nearness.
    """
    adjacency = graph.adjacency("material")
    makes = manufacturing or set()
    groups = [list(c) for c in clusters]

    def reaches(seed: list[str]) -> set[str]:
        seen, frontier, out = set(seed), list(seed), set()
        while frontier:
            nxt = []
            for node in frontier:
                for edge in adjacency.get(node, ()):
                    other = edge.other(node)
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
                    if graph.is_machine(other):
                        out.add(other)
            frontier = nxt
        return out

    def first_arrival(seed: list[str], owner: dict[str, int], self_id: int) -> dict[int, int]:
        """Hop depth at which each other cluster is first reached."""
        held = set(seed)
        seen = set(seed)
        queue = deque((m, 0) for m in seed)
        out: dict[int, int] = {}
        while queue:
            node, depth = queue.popleft()
            if node not in held and graph.is_machine(node):
                target = owner.get(node)
                if target is not None and target != self_id and target not in out:
                    out[target] = depth
            for edge in adjacency.get(node, ()):
                other = edge.other(node)
                if other not in seen:
                    seen.add(other)
                    queue.append((other, depth + 1))
        return out

    for _ in range(rounds):
        owner = {m: k for k, c in enumerate(groups) for m in c}
        wanted: dict[int, int] = {}
        for k, members in enumerate(groups):
            if sum(1 for m in members if m in makes) > max_recipes:
                continue
            held = set(members)
            outside = reaches(members) - held
            if not outside:
                continue
            targets = Counter(owner[m] for m in outside if m in owner)
            targets.pop(k, None)
            if not targets:
                continue

            best, hits = targets.most_common(1)[0]
            if hits / len(outside) < min_exclusivity:
                # Not embedded in one cluster -- but it may still sit at the end of a
                # belt that plainly leads somewhere. Fall back to first arrival.
                order = sorted(first_arrival(members, owner, k).items(), key=lambda kv: kv[1])
                if not order:
                    continue
                if len(order) > 1 and order[1][1] < order[0][1] * nearest_margin:
                    continue  # too close to call
                best = order[0][0]
            if len(members) > max_ratio * len(groups[best]):
                continue
            wanted[k] = best
        if not wanted:
            break
        parent = list(range(len(groups)))

        def find(x: int, parent: list[int] = parent) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for child, host in wanted.items():
            a, b = find(child), find(host)
            if a != b:
                parent[a] = b
        merged: dict[int, list[str]] = defaultdict(list)
        for k, members in enumerate(groups):
            merged[find(k)] += members
        groups = list(merged.values())
    return groups


def propose(
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    structures: Structures,
    machines: list[str] | None = None,
    weights: dict[str, float] | None = None,
    max_span_m: float = MAX_SPAN_M,
    prior: float = PRIOR,
    attach: bool = True,
) -> list[Proposal]:
    """Agglomerate machines into proposed factories, most cohesive first.

    Seeded from foundation slabs rather than from singletons: slabs score precision 1.000
    as a same-factory signal, so starting there costs nothing and starts the agglomeration
    a long way along.
    """
    weights = {**WEIGHTS, **(weights or {})}
    pool = sorted(machines if machines is not None else graph.machines())
    if not pool:
        return []

    features = _feature_fn(graph, game, projection, structures)
    index = {m: i for i, m in enumerate(pool)}

    # Pairwise scores, computed once. -inf past the span cap so no linkage can cross it.
    pair: dict[tuple[int, int], float] = {}
    fired: dict[tuple[int, int], tuple[str, ...]] = {}
    for i, a in enumerate(pool):
        for j in range(i + 1, len(pool)):
            b = pool[j]
            feats, distance_m = features(a, b)
            if distance_m > max_span_m:
                pair[(i, j)] = -math.inf
                continue
            pair[(i, j)] = sum(weights[k] * v for k, v in feats.items()) - prior
            fired[(i, j)] = tuple(k for k, v in feats.items() if v)

    def score(i: int, j: int) -> float:
        return pair[(i, j)] if i < j else pair[(j, i)]

    slab_seed: dict[int, list[int]] = defaultdict(list)
    clusters: list[list[int]] = []
    seeded: list[str] = []
    for m in pool:
        if m in structures.slab_of:
            slab_seed[structures.slab_of[m]].append(index[m])
        else:
            clusters.append([index[m]])
            seeded.append("ground")
    for slab_id, members in sorted(slab_seed.items()):
        clusters.append(members)
        seeded.append(f"slab:{slab_id}")

    # Complete linkage: merge only when EVERY cross pair clears the bar. Single linkage
    # on the identical score collapses to F1 0.521, because one adjacent pair is enough
    # to chain a whole base together.
    link: dict[tuple[int, int], float] = {}
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            link[(i, j)] = min(score(a, b) for a in clusters[i] for b in clusters[j])

    alive = set(range(len(clusters)))
    # Seeded from each seed's own weakest pair rather than from infinity: a slab seed that
    # never merges is a proposal like any other and has a real cohesion to report.
    cohesion = {
        i: min((score(a, b) for x, a in enumerate(c) for b in c[x + 1 :]), default=math.inf)
        for i, c in enumerate(clusters)
    }
    while True:
        best, target = 0.0, None
        for (i, j), value in link.items():
            if i in alive and j in alive and value > best:
                best, target = value, (i, j)
        if target is None:
            break
        i, j = target
        clusters[i] = clusters[i] + clusters[j]
        seeded[i] = seeded[i] if seeded[i].startswith("slab") else seeded[j]
        cohesion[i] = min(cohesion[i], cohesion[j], best)
        alive.discard(j)
        for k in alive:
            if k == i:
                continue
            a, b = (min(i, k), max(i, k)), (min(j, k), max(j, k))
            link[a] = min(link.get(a, math.inf), link.get(b, math.inf))

    linked = [[pool[x] for x in clusters[i]] for i in sorted(alive)]
    seeds = {frozenset(c): seeded[i] for i, c in zip(sorted(alive), linked, strict=False)}
    weakest = {frozenset(c): cohesion[i] for i, c in zip(sorted(alive), linked, strict=False)}
    pieces = {frozenset(c): len(c) for c in linked}
    manufacturing = {
        r["instance"].rsplit(".", 1)[-1] for r in projection.get("machines", ()) if r.get("recipe")
    }
    final = attach_dependents(linked, graph, manufacturing) if attach else linked

    out: list[Proposal] = []
    for members in final:
        held = frozenset(members)
        parts = sorted((n for c, n in pieces.items() if c <= held), reverse=True) or [len(members)]
        evidence: Counter = Counter()
        ids = [index[m] for m in members]
        for x, a in enumerate(ids):
            for b in ids[x + 1 :]:
                for name in fired.get((min(a, b), max(a, b)), ()):
                    evidence[name] += 1
        inner = next((v for c, v in weakest.items() if c <= held), math.inf)
        out.append(
            Proposal(
                machines=sorted(members),
                cohesion=0.0 if math.isinf(inner) else inner,
                evidence=evidence,
                seeded_by=next((s for c, s in seeds.items() if c <= held), ""),
                parts=parts,
            )
        )
    out.sort(key=lambda p: -p.size)
    return out
