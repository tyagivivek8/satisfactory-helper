import type {
  FloorPlatform,
  Landmark,
  Machine,
  RouteSegment,
  StoragePlacement,
  Structure,
  Workspace,
} from "../types";

export interface FactorySceneData {
  machines: Machine[];
  landmarks: Landmark[];
  structures: Structure[];
  storage: StoragePlacement[];
  belts: RouteSegment[];
  pipes: RouteSegment[];
  originX: number;
  originY: number;
  minZ: number;
  maxZ: number;
  horizontalSpan: number;
  contentSpan: number;
  focusX: number;
  focusY: number;
}

export type StructureKind = "foundation" | "ramp" | "wall" | "railing";

export interface StructureShape {
  kind: StructureKind;
  width: number;
  height: number;
  depth: number;
  verticalOffset: number;
}

function machineIds(platform: FloorPlatform | null, floor: number | null): Set<string> | null {
  if (!platform) return null;
  const bands = floor === null
    ? platform.bands
    : platform.bands.filter((band) => band.ordinal === floor);
  return new Set(bands.flatMap((band) => band.machines));
}

function extractorBelongs(machine: Machine, platform: FloorPlatform | null): boolean {
  if (!platform) return true;
  const [centreX, centreY] = platform.centre_m;
  const [extentX, extentY] = platform.extent_m;
  if (
    machine.x_m === null ||
    machine.y_m === null ||
    centreX === null ||
    centreY === null ||
    extentX === null ||
    extentY === null
  ) {
    return false;
  }
  const contextMargin = 120;
  return (
    Math.abs(machine.x_m - centreX) <= extentX / 2 + contextMargin &&
    Math.abs(machine.y_m - centreY) <= extentY / 2 + contextMargin
  );
}

function landmarkBelongs(landmark: Landmark, platform: FloorPlatform | null): boolean {
  if (!platform) return true;
  const [centreX, centreY] = platform.centre_m;
  const [extentX, extentY] = platform.extent_m;
  if (centreX === null || centreY === null || extentX === null || extentY === null) {
    return false;
  }
  const margin = 120;
  return (
    Math.abs(landmark.x_m - centreX) <= extentX / 2 + margin &&
    Math.abs(landmark.y_m - centreY) <= extentY / 2 + margin
  );
}

function structureRows(platform: FloorPlatform | null, floor: number | null): Set<number> | null {
  if (!platform) return null;
  const bands = floor === null
    ? platform.bands
    : platform.bands.filter((band) => band.ordinal === floor);
  return new Set(bands.flatMap((band) => band.deck_rows));
}

interface DeckAnchor {
  platform: number;
  floor: number;
  x: number;
  y: number;
  z: number;
}

function deckAnchors(workspace: Workspace): DeckAnchor[] {
  const anchors: DeckAnchor[] = [];
  for (const candidate of workspace.floors.platforms) {
    for (const band of candidate.bands) {
      for (const row of band.deck_rows) {
        const structure = workspace.structures.structures[row];
        if (!structure) continue;
        anchors.push({
          platform: candidate.index,
          floor: band.ordinal,
          x: structure.x_m,
          y: structure.y_m,
          z: band.top_m ?? structure.z_m,
        });
      }
    }
  }
  return anchors;
}

function nearestDeck(structure: Structure, anchors: DeckAnchor[]): DeckAnchor | null {
  let nearest: DeckAnchor | null = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  for (const anchor of anchors) {
    const dx = structure.x_m - anchor.x;
    const dy = structure.y_m - anchor.y;
    const dz = structure.z_m - anchor.z;
    const distance = dx * dx + dy * dy + dz * dz * 4;
    if (distance < nearestDistance) {
      nearest = anchor;
      nearestDistance = distance;
    }
  }
  return nearest;
}

function structureBelongs(
  structure: Structure,
  row: number,
  selectedRows: Set<number> | null,
  platform: FloorPlatform | null,
  floor: number | null,
  anchors: DeckAnchor[],
): boolean {
  if (!platform || !selectedRows) return true;
  if (selectedRows.has(row)) return true;
  if (structureShape(structure.cls).kind === "foundation") return false;
  const owner = nearestDeck(structure, anchors);
  return owner?.platform === platform.index && (floor === null || owner.floor === floor);
}

function storageBelongs(
  platform: FloorPlatform | null,
  floor: number | null,
  placement: StoragePlacement,
): boolean {
  if (!platform || placement.x_m === null || placement.y_m === null || placement.z_m === null) {
    return false;
  }
  if (placement.platform !== platform.index) return false;
  return floor === null || placement.global_floor === floor;
}

function routeBelongs(
  route: RouteSegment,
  platform: FloorPlatform | null,
  floor: number | null,
): boolean {
  if (!platform) return true;
  const [centreX, centreY] = platform.centre_m;
  const [extentX, extentY] = platform.extent_m;
  if (centreX === null || centreY === null || extentX === null || extentY === null) return true;

  const margin = floor === null ? 140 : 64;
  const floorBand = floor === null
    ? null
    : platform.bands.find((band) => band.ordinal === floor) ?? null;

  return route.points_m.some(([x, y, z]) => {
    const inside =
      Math.abs(x - centreX) <= extentX / 2 + margin &&
      Math.abs(y - centreY) <= extentY / 2 + margin;
    if (!inside) return false;
    if (!floorBand || floorBand.top_m === null) return true;
    return Math.abs(z - floorBand.top_m) <= 7;
  });
}

function finite(values: Array<number | null | undefined>): number[] {
  return values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
}

export function collectFactorySceneData(
  workspace: Workspace,
  platform: FloorPlatform | null,
  floor: number | null,
): FactorySceneData {
  const ids = machineIds(platform, floor);
  const rows = structureRows(platform, floor);
  const anchors = deckAnchors(workspace);
  const floorMachines = [
    ...workspace.machines.machines,
    ...workspace.machines.generators,
  ].filter(
    (machine) =>
      machine.x_m !== null &&
      machine.y_m !== null &&
      machine.z_m !== null &&
      (!ids || ids.has(machine.instance_leaf)),
  );
  const extractors = workspace.machines.extractors.filter(
    (machine) =>
      machine.x_m !== null &&
      machine.y_m !== null &&
      machine.z_m !== null &&
      extractorBelongs(machine, platform),
  );
  const machines = [...floorMachines, ...extractors];
  const landmarks = (workspace.landmarks ?? []).filter((landmark) =>
    landmarkBelongs(landmark, platform),
  );
  const structures = workspace.structures.structures.filter((structure, index) =>
    structureBelongs(structure, index, rows, platform, floor, anchors),
  );
  const storage = workspace.storage.storage.filter((placement) =>
    storageBelongs(platform, floor, placement),
  );
  const belts = workspace.belts.belts.filter((route) => routeBelongs(route, platform, floor));
  const pipes = workspace.pipes.pipes.filter((route) => routeBelongs(route, platform, floor));

  const originX = platform?.centre_m[0] ?? 0;
  const originY = platform?.centre_m[1] ?? 0;
  const zValues = finite([
    ...machines.map((machine) => machine.z_m),
    ...landmarks.flatMap((landmark) => [
      landmark.z_m,
      landmark.z_m + (landmark.cls === "Build_SpaceElevator_C" ? 110 : landmark.h_m ?? 20),
    ]),
    ...structures.map((structure) => structure.z_m),
    ...storage.map((placement) => placement.z_m),
    ...belts.flatMap((route) => route.points_m.map((point) => point[2])),
    ...pipes.flatMap((route) => route.points_m.map((point) => point[2])),
  ]);
  const minZ = zValues.length ? Math.min(...zValues) : 0;
  const maxZ = zValues.length ? Math.max(...zValues) : 40;
  const horizontalSpan = Math.max(
    platform?.extent_m[0] ?? 0,
    platform?.extent_m[1] ?? 0,
    120,
  );
  const xValues = finite([
    ...machines.map((machine) => machine.x_m),
    ...landmarks.map((landmark) => landmark.x_m),
    ...structures.map((structure) => structure.x_m),
    ...storage.map((placement) => placement.x_m),
  ]);
  const yValues = finite([
    ...machines.map((machine) => machine.y_m),
    ...landmarks.map((landmark) => landmark.y_m),
    ...structures.map((structure) => structure.y_m),
    ...storage.map((placement) => placement.y_m),
  ]);
  const minX = xValues.length ? Math.min(...xValues) : originX - horizontalSpan / 2;
  const maxX = xValues.length ? Math.max(...xValues) : originX + horizontalSpan / 2;
  const minY = yValues.length ? Math.min(...yValues) : originY - horizontalSpan / 2;
  const maxY = yValues.length ? Math.max(...yValues) : originY + horizontalSpan / 2;
  const contentSpan = Math.max(maxX - minX + 48, maxY - minY + 48, 120);
  const focusX = (minX + maxX) / 2 - originX;
  const focusY = (minY + maxY) / 2 - originY;

  return {
    machines,
    landmarks,
    structures,
    storage,
    belts,
    pipes,
    originX,
    originY,
    minZ,
    maxZ,
    horizontalSpan,
    contentSpan,
    focusX,
    focusY,
  };
}

export function structureShape(cls: string | null): StructureShape {
  const name = cls ?? "";
  if (name.includes("Railing")) {
    // The shipped mesh is 4 m along local Y and about 0.23 m along local X.
    return {
      kind: "railing",
      width: 0.23,
      height: 1.28,
      depth: 4,
      verticalOffset: 0.46,
    };
  }
  if (name.includes("Wall")) {
    // Build_Wall_8x4_01 chains at 8 m intervals along local Y when yaw is zero.
    return { kind: "wall", width: 0.5, height: 4, depth: 8, verticalOffset: 2 };
  }
  if (name.includes("Ramp")) {
    return { kind: "ramp", width: 8, height: 4, depth: 8, verticalOffset: 0 };
  }
  const thickness = name.includes("8x1") ? 1 : name.includes("8x2") ? 2 : 4;
  return {
    kind: "foundation",
    width: 8,
    height: thickness,
    depth: 8,
    verticalOffset: 0,
  };
}

export function worldToScene(
  x: number,
  y: number,
  z: number,
  data: Pick<FactorySceneData, "originX" | "originY">,
): [number, number, number] {
  return [x - data.originX, z, y - data.originY];
}
