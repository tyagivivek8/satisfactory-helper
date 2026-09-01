import { describe, expect, it } from "vitest";
import type { MapInfo } from "../types";
import { rampGeometry, sceneMapZoom } from "./FactoryScene3D";

const mapInfo: MapInfo = {
  available: true,
  source: "installed_game",
  bounds: { x_min_m: -3247, x_max_m: 4253, y_min_m: -3750, y_max_m: 3750 },
  tile_px: 256,
  max_z: 5,
  dense_tile_px: 512,
  dense_max_z: 4,
  version: "test",
  reason: null,
};

describe("3D ramp geometry", () => {
  it("rises toward the shipped ramp mesh's local negative X axis", () => {
    const geometry = rampGeometry();
    const positions = geometry.getAttribute("position");
    const negativeXHeights: number[] = [];
    const positiveXHeights: number[] = [];

    for (let index = 0; index < positions.count; index += 1) {
      const heights = positions.getX(index) < 0 ? negativeXHeights : positiveXHeights;
      heights.push(positions.getY(index));
    }

    expect(Math.max(...negativeXHeights)).toBe(0.5);
    expect(Math.max(...positiveXHeights)).toBe(-0.5);
    geometry.dispose();
  });

  it("does not overfetch zoom-5 tiles for a broad factory view", () => {
    expect(sceneMapZoom(mapInfo, 2_200, 900)).toBe(4);
  });

  it("keeps zoom-5 detail for a tightly framed site", () => {
    expect(sceneMapZoom(mapInfo, 700, 900)).toBe(5);
  });
});
