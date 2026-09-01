import { describe, expect, it } from "vitest";
import { canvasYToWorldY, worldYToCanvasY } from "./factoryCanvasGeometry";

describe("factory canvas north-up transform", () => {
  it("draws Satisfactory's negative-Y north above positive-Y south", () => {
    expect(worldYToCanvasY(-100, 0, 2, 600)).toBeLessThan(
      worldYToCanvasY(100, 0, 2, 600),
    );
  });

  it("converts screen coordinates back to the same world Y", () => {
    const screenY = worldYToCanvasY(-418.5, 30, 1.6, 560);
    expect(canvasYToWorldY(screenY, 30, 1.6, 560)).toBeCloseTo(-418.5);
  });
});
