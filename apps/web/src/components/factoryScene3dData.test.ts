import { describe, expect, it } from "vitest";
import type { FloorPlatform, Workspace } from "../types";
import {
  collectFactorySceneData,
  structureShape,
  worldToScene,
} from "./factoryScene3dData";

const platform: FloorPlatform = {
  index: 0,
  area_m2: 4_000,
  centre_m: [50, 150],
  extent_m: [200, 200],
  clean: 1,
  label: null,
  bands: [
    {
      ordinal: 23,
      top_m: 30,
      minor: false,
      area_m2: 2_000,
      machines: ["m23"],
      attachments: [],
      deck_rows: [0],
      machine_count: 1,
      attachment_count: 0,
    },
    {
      ordinal: 43,
      top_m: 70,
      minor: false,
      area_m2: 2_000,
      machines: ["m43"],
      attachments: [],
      deck_rows: [1],
      machine_count: 1,
      attachment_count: 0,
    },
  ],
};

const workspace = {
  machines: {
    machines: [
      { instance_leaf: "m23", x_m: 20, y_m: 120, z_m: 30 },
      { instance_leaf: "m43", x_m: 104, y_m: 204, z_m: 70 },
    ],
    extractors: [
      { instance_leaf: "miner-near", cls: "Build_MinerMk2_C", x_m: 180, y_m: 150, z_m: 3 },
      { instance_leaf: "miner-far", cls: "Build_MinerMk2_C", x_m: 900, y_m: 900, z_m: 3 },
    ],
    generators: [],
  },
  landmarks: [],
  floors: { platforms: [platform] },
  structures: {
    structures: [
      { cls: "Build_Foundation_8x4_01_C", x_m: 16, y_m: 116, z_m: 28, yaw: 0 },
      { cls: "Build_Foundation_8x4_01_C", x_m: 100, y_m: 200, z_m: 68, yaw: 0 },
      { cls: "Build_Ramp_8x4_01_C", x_m: 108, y_m: 200, z_m: 70, yaw: 90 },
    ],
    count: 3,
    tile_m: 8,
  },
  storage: { storage: [], count: 0, filled: 0, items_total: 0 },
  belts: {
    belts: [
      { name: "near", points_m: [[100, 200, 70], [108, 200, 70]] },
      { name: "wrong-floor", points_m: [[100, 200, 30], [108, 200, 30]] },
      { name: "far", points_m: [[900, 900, 70], [910, 900, 70]] },
    ],
    count: 3,
    chains: 3,
  },
  pipes: { pipes: [], count: 0, networks: 0 },
} as unknown as Workspace;

describe("3D factory scene data", () => {
  it("isolates the selected floor and fits to its visible geometry", () => {
    const data = collectFactorySceneData(workspace, platform, 43);

    expect(data.machines.map((machine) => machine.instance_leaf)).toEqual([
      "m43",
      "miner-near",
    ]);
    expect(data.structures.map((structure) => structure.cls)).toEqual([
      "Build_Foundation_8x4_01_C",
      "Build_Ramp_8x4_01_C",
    ]);
    expect(data.belts.map((route) => route.name)).toEqual(["near"]);
    expect(data.focusX).toBe(90);
    expect(data.focusY).toBe(27);
    expect(data.contentSpan).toBe(128);
  });

  it("maps Satisfactory XYZ into the Three.js X-up-Z scene", () => {
    const data = collectFactorySceneData(workspace, platform, 43);

    expect(worldToScene(100, 200, 70, data)).toEqual([50, 70, 50]);
  });

  it("uses real foundation thicknesses and distinct vertical pieces", () => {
    expect(structureShape("Build_Foundation_8x1_01_C")).toMatchObject({ height: 1 });
    expect(structureShape("Build_Foundation_8x2_01_C")).toMatchObject({ height: 2 });
    expect(structureShape("Build_Foundation_8x4_01_C")).toMatchObject({ height: 4 });
    expect(structureShape("Build_Wall_8x4_01_C").kind).toBe("wall");
    expect(structureShape("Build_Ramp_8x4_01_C").kind).toBe("ramp");
    expect(structureShape("Build_Wall_8x4_01_C")).toMatchObject({
      width: 0.5,
      depth: 8,
    });
    expect(structureShape("Build_Railing_01_C")).toMatchObject({
      width: 0.23,
      height: 1.28,
      depth: 4,
    });
  });
});
