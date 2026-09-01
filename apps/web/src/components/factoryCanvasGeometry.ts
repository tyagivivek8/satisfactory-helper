export function worldYToCanvasY(
  worldY: number,
  centreY: number,
  scale: number,
  canvasHeight: number,
): number {
  return (worldY - centreY) * scale + canvasHeight / 2;
}

export function canvasYToWorldY(
  canvasY: number,
  centreY: number,
  scale: number,
  canvasHeight: number,
): number {
  return centreY + (canvasY - canvasHeight / 2) / scale;
}
