import { Crosshair, Focus, Map as MapIcon, Navigation, Route, ScanSearch } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type {
  FloorPlatform,
  MapInfo,
  Machine,
  PlanAction,
  PlanAnswer,
  Structure,
  Workspace,
} from "../types";
import { canvasYToWorldY, worldYToCanvasY } from "./factoryCanvasGeometry";
import { Button } from "./ui/button";

interface ViewState {
  cx: number;
  cy: number;
  scale: number;
}

export interface FactoryCanvasProps {
  workspace: Workspace;
  mapInfo: MapInfo | null;
  platform: FloorPlatform | null;
  selectedFloor: number | null;
  plan: PlanAnswer | null;
  activeAction: PlanAction | null;
  selectedMachine: Machine | null;
  onSelectMachine: (machine: Machine | null) => void;
}

interface CachedTile {
  image: HTMLImageElement;
  ready: boolean;
  failed: boolean;
}

const machineColors: Record<string, string> = {
  producing: "oklch(0.72 0.14 145)",
  saturated: "oklch(0.73 0.13 215)",
  blocked: "oklch(0.78 0.15 82)",
  starved: "oklch(0.62 0.2 28)",
  idle: "oklch(0.58 0.02 60)",
  paused: "oklch(0.5 0 0)",
};

function machineSet(platform: FloorPlatform | null, floor: number | null): Set<string> | null {
  if (!platform) return null;
  const bands = floor === null ? platform.bands : platform.bands.filter((band) => band.ordinal === floor);
  return new Set(bands.flatMap((band) => band.machines));
}

function structureRows(platform: FloorPlatform | null, floor: number | null): Set<number> | null {
  if (!platform) return null;
  const bands = floor === null ? platform.bands : platform.bands.filter((band) => band.ordinal === floor);
  return new Set(bands.flatMap((band) => band.deck_rows));
}

function fitView(platform: FloorPlatform | null): ViewState {
  const cx = platform?.centre_m[0] ?? 0;
  const cy = platform?.centre_m[1] ?? 0;
  const longest = Math.max(platform?.extent_m[0] ?? 600, platform?.extent_m[1] ?? 600, 80);
  return { cx: cx ?? 0, cy: cy ?? 0, scale: Math.min(1.4, 620 / longest) };
}

function mapZoom(mapInfo: MapInfo, scale: number): number {
  const worldWidth = mapInfo.bounds.x_max_m - mapInfo.bounds.x_min_m;
  const desiredPixels = worldWidth * scale;
  return Math.max(
    0,
    Math.min(mapInfo.max_z, Math.ceil(Math.log2(Math.max(1, desiredPixels / mapInfo.tile_px)))),
  );
}

function storageBelongsToView(
  platform: FloorPlatform | null,
  floor: number | null,
  placement: Workspace["storage"]["storage"][number],
): boolean {
  if (!platform || placement.x_m === null || placement.y_m === null || placement.z_m === null) {
    return false;
  }
  if (placement.platform !== platform.index) return false;
  if (floor === null) return true;
  return placement.global_floor === floor;
}

export function FactoryCanvas({
  workspace,
  mapInfo,
  platform,
  selectedFloor,
  plan,
  activeAction,
  selectedMachine,
  onSelectMachine,
}: FactoryCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; view: ViewState } | null>(null);
  const tileCache = useRef<Map<string, CachedTile>>(new Map());
  const [view, setView] = useState<ViewState>(() => fitView(platform));
  const [size, setSize] = useState({ width: 900, height: 560 });
  const [tileRevision, setTileRevision] = useState(0);

  const allMachines = useMemo(
    () => [
      ...workspace.machines.machines,
      ...workspace.machines.extractors,
      ...workspace.machines.generators,
    ],
    [workspace.machines],
  );
  const visibleIds = useMemo(() => machineSet(platform, selectedFloor), [platform, selectedFloor]);
  const visibleRows = useMemo(
    () => structureRows(platform, selectedFloor),
    [platform, selectedFloor],
  );
  const visibleMachines = useMemo(
    () =>
      allMachines.filter(
        (machine) =>
          machine.x_m !== null && machine.y_m !== null && (!visibleIds || visibleIds.has(machine.instance_leaf)),
      ),
    [allMachines, visibleIds],
  );
  const visibleStructures = useMemo(
    () =>
      workspace.structures.structures.filter((_, index) => !visibleRows || visibleRows.has(index)),
    [visibleRows, workspace.structures.structures],
  );
  const visibleStorage = useMemo(
    () =>
      workspace.storage.storage.filter((placement) =>
        storageBelongsToView(platform, selectedFloor, placement),
      ),
    [platform, selectedFloor, workspace.storage.storage],
  );

  useEffect(() => setView(fitView(platform)), [platform]);

  useEffect(() => {
    tileCache.current.clear();
    setTileRevision((current) => current + 1);
  }, [mapInfo?.version]);

  useEffect(() => {
    if (!shellRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(shellRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 0.86 : 1.16;
      setView((current) => ({
        ...current,
        scale: Math.min(6, Math.max(0.08, current.scale * factor)),
      }));
    };
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(size.width * ratio);
    canvas.height = Math.round(size.height * ratio);
    const context = canvas.getContext("2d");
    if (!context) return;
    context.scale(ratio, ratio);
    context.clearRect(0, 0, size.width, size.height);
    context.fillStyle = "oklch(0.105 0 0)";
    context.fillRect(0, 0, size.width, size.height);

    const sx = (x: number) => (x - view.cx) * view.scale + size.width / 2;
    const sy = (y: number) => worldYToCanvasY(y, view.cy, view.scale, size.height);

    if (mapInfo?.available) {
      const zoom = mapZoom(mapInfo, view.scale);
      const span = 1 << zoom;
      const tileWidthM = (mapInfo.bounds.x_max_m - mapInfo.bounds.x_min_m) / span;
      const tileHeightM = (mapInfo.bounds.y_max_m - mapInfo.bounds.y_min_m) / span;
      const leftM = view.cx - size.width / (2 * view.scale);
      const rightM = view.cx + size.width / (2 * view.scale);
      const topM = view.cy - size.height / (2 * view.scale);
      const bottomM = view.cy + size.height / (2 * view.scale);
      const minX = Math.max(0, Math.floor((leftM - mapInfo.bounds.x_min_m) / tileWidthM));
      const maxX = Math.min(span - 1, Math.floor((rightM - mapInfo.bounds.x_min_m) / tileWidthM));
      const minY = Math.max(0, Math.floor((topM - mapInfo.bounds.y_min_m) / tileHeightM));
      const maxY = Math.min(span - 1, Math.floor((bottomM - mapInfo.bounds.y_min_m) / tileHeightM));
      const density =
        window.devicePixelRatio >= 1.5 && mapInfo.dense_max_z !== null && zoom <= mapInfo.dense_max_z
          ? 2
          : 1;

      context.save();
      context.globalAlpha = 0.72;
      for (let tileY = minY; tileY <= maxY; tileY += 1) {
        for (let tileX = minX; tileX <= maxX; tileX += 1) {
          const key = `${mapInfo.version}:${density}:${zoom}:${tileX}:${tileY}`;
          let tile = tileCache.current.get(key);
          if (!tile) {
            const image = new Image();
            tile = { image, ready: false, failed: false };
            tileCache.current.set(key, tile);
            image.onload = () => {
              tile!.ready = true;
              setTileRevision((current) => current + 1);
            };
            image.onerror = () => {
              tile!.failed = true;
              setTileRevision((current) => current + 1);
            };
            image.src = `/api/maptiles/${zoom}/${tileX}/${tileY}?density=${density}&v=${mapInfo.version}`;
          }
          if (!tile.ready || tile.failed) continue;
          const worldX = mapInfo.bounds.x_min_m + tileX * tileWidthM;
          const worldY = mapInfo.bounds.y_min_m + tileY * tileHeightM;
          context.drawImage(
            tile.image,
            sx(worldX),
            sy(worldY),
            tileWidthM * view.scale + 0.75,
            tileHeightM * view.scale + 0.75,
          );
        }
      }
      context.restore();
      context.fillStyle = "oklch(0.105 0 0 / 0.42)";
      context.fillRect(0, 0, size.width, size.height);
    }

    const majorGrid = 40 * view.scale;
    if (majorGrid >= 10) {
      context.strokeStyle = "oklch(0.7 0.018 60 / 0.16)";
      context.lineWidth = 1;
      const xOffset = ((-view.cx * view.scale + size.width / 2) % majorGrid + majorGrid) % majorGrid;
      const yOffset = ((-view.cy * view.scale + size.height / 2) % majorGrid + majorGrid) % majorGrid;
      context.beginPath();
      for (let x = xOffset; x < size.width; x += majorGrid) {
        context.moveTo(x, 0);
        context.lineTo(x, size.height);
      }
      for (let y = yOffset; y < size.height; y += majorGrid) {
        context.moveTo(0, y);
        context.lineTo(size.width, y);
      }
      context.stroke();
    }

    const dotGrid = 8 * view.scale;
    if (dotGrid >= 6) {
      const xOffset = ((-view.cx * view.scale + size.width / 2) % dotGrid + dotGrid) % dotGrid;
      const yOffset = ((-view.cy * view.scale + size.height / 2) % dotGrid + dotGrid) % dotGrid;
      context.fillStyle = "oklch(0.82 0.02 65 / 0.22)";
      for (let x = xOffset; x < size.width; x += dotGrid) {
        for (let y = yOffset; y < size.height; y += dotGrid) {
          context.fillRect(Math.round(x), Math.round(y), 1, 1);
        }
      }
    }

    context.fillStyle = "oklch(0.24 0.01 50 / 0.72)";
    const tile = Math.max(1, workspace.structures.tile_m * view.scale - 0.7);
    for (const piece of visibleStructures) {
      context.save();
      context.translate(sx(piece.x_m), sy(piece.y_m));
      context.rotate(((piece.yaw ?? 0) * Math.PI) / 180);
      context.fillRect(-tile / 2, -tile / 2, tile, tile);
      context.restore();
    }

    function drawRoutes(
      routeContext: CanvasRenderingContext2D,
      routes: typeof workspace.belts.belts,
      color: string,
      width: number,
    ) {
      routeContext.strokeStyle = color;
      routeContext.lineWidth = width;
      routeContext.lineCap = "round";
      routeContext.lineJoin = "round";
      for (const route of routes) {
        if (route.points_m.length < 2) continue;
        const inViewport = route.points_m.some(
          ([x, y]) => Math.abs(x - view.cx) < size.width / view.scale && Math.abs(y - view.cy) < size.height / view.scale,
        );
        if (!inViewport) continue;
        routeContext.beginPath();
        route.points_m.forEach(([x, y], index) => {
          if (index === 0) routeContext.moveTo(sx(x), sy(y));
          else routeContext.lineTo(sx(x), sy(y));
        });
        routeContext.stroke();
      }
    }
    drawRoutes(context, workspace.belts.belts, "oklch(0.59 0.035 72 / 0.72)", Math.max(1, view.scale));
    drawRoutes(context, workspace.pipes.pipes, "oklch(0.62 0.09 225 / 0.76)", Math.max(1.2, view.scale * 1.25));

    for (const placement of visibleStorage) {
      const width = Math.max(5, (placement.w_m ?? 5) * view.scale);
      const depth = Math.max(5, (placement.l_m ?? 10) * view.scale);
      context.save();
      context.translate(sx(placement.x_m!), sy(placement.y_m!));
      context.rotate(((placement.yaw ?? 0) * Math.PI) / 180);
      context.fillStyle = "oklch(0.5 0.055 300 / 0.72)";
      context.strokeStyle = "oklch(0.76 0.07 300 / 0.9)";
      context.lineWidth = 1;
      context.fillRect(-width / 2, -depth / 2, width, depth);
      context.strokeRect(-width / 2, -depth / 2, width, depth);
      context.restore();
    }

    for (const machine of visibleMachines) {
      const width = Math.max(5, (machine.w_m ?? 6) * view.scale);
      const depth = Math.max(5, (machine.l_m ?? 8) * view.scale);
      context.save();
      context.translate(sx(machine.x_m!), sy(machine.y_m!));
      context.rotate(((machine.yaw ?? 0) * Math.PI) / 180);
      context.fillStyle = machineColors[machine.paused ? "paused" : machine.state] ?? machineColors.idle;
      context.globalAlpha = machine.instance_leaf === selectedMachine?.instance_leaf ? 1 : 0.86;
      context.fillRect(-width / 2, -depth / 2, width, depth);
      context.strokeStyle = "oklch(0.08 0 0 / 0.82)";
      context.lineWidth = 1;
      context.strokeRect(-width / 2, -depth / 2, width, depth);
      if (machine.instance_leaf === selectedMachine?.instance_leaf) {
        context.strokeStyle = "oklch(0.95 0.01 75)";
        context.lineWidth = 2;
        context.strokeRect(-width / 2 - 2, -depth / 2 - 2, width + 4, depth + 4);
      }
      context.restore();
    }

    const plotted = plan?.actions.filter((action) => action.coordinates) ?? [];
    for (const action of plotted) {
      const point = action.coordinates!;
      const color =
        action.kind === "reroute"
          ? "oklch(0.73 0.13 215)"
          : action.kind === "move"
            ? "oklch(0.78 0.15 82)"
            : action.kind === "remove"
              ? "oklch(0.7 0 0)"
              : "oklch(0.64 0.19 27.2)";
      context.beginPath();
      context.arc(sx(point.x_m), sy(point.y_m), action.id === activeAction?.id ? 12 : 8, 0, Math.PI * 2);
      context.strokeStyle = color;
      context.lineWidth = action.id === activeAction?.id ? 3 : 2;
      context.setLineDash(action.kind === "remove" ? [4, 3] : []);
      context.stroke();
      context.setLineDash([]);
    }

    context.fillStyle = "oklch(0.13 0 0 / 0.88)";
    context.fillRect(10, size.height - 50, 98, 40);
    context.strokeStyle = "oklch(0.31 0.012 27.2 / 0.9)";
    context.strokeRect(10.5, size.height - 49.5, 97, 39);
    context.fillStyle = "oklch(0.77 0.018 60)";
    context.font = "11px Cascadia Mono, monospace";
    context.fillText(`${Math.round(80 / view.scale)} m`, 18, size.height - 18);
    context.strokeStyle = "oklch(0.77 0.018 60)";
    context.beginPath();
    context.moveTo(18, size.height - 32);
    context.lineTo(98, size.height - 32);
    context.stroke();
  }, [activeAction, mapInfo, plan, selectedMachine, size, tileRevision, view, visibleMachines, visibleStorage, visibleStructures, workspace]);

  function onPointerDown(event: ReactPointerEvent<HTMLCanvasElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, view };
  }

  function onPointerMove(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!dragRef.current) return;
    const drag = dragRef.current;
    setView({
      ...drag.view,
      cx: drag.view.cx - (event.clientX - drag.x) / drag.view.scale,
      cy: drag.view.cy - (event.clientY - drag.y) / drag.view.scale,
    });
  }

  function onPointerUp(event: ReactPointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag || Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const worldX = view.cx + (event.clientX - bounds.left - size.width / 2) / view.scale;
    const worldY = canvasYToWorldY(
      event.clientY - bounds.top,
      view.cy,
      view.scale,
      size.height,
    );
    const nearest = visibleMachines.reduce<{ machine: Machine; distance: number } | null>((best, machine) => {
      const distance = Math.hypot(machine.x_m! - worldX, machine.y_m! - worldY);
      return !best || distance < best.distance ? { machine, distance } : best;
    }, null);
    onSelectMachine(nearest && nearest.distance * view.scale < 18 ? nearest.machine : null);
  }

  return (
    <div className="canvas-shell" ref={shellRef}>
      <canvas
        ref={canvasRef}
        aria-label="Measured factory layout. Drag to pan and scroll to zoom."
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
      <div className="canvas-corner canvas-corner--left">
        <span className="canvas-coordinate-readout"><Crosshair aria-hidden="true" size={14} /> Measured world coordinates</span>
        <span className="canvas-map-readout"><MapIcon aria-hidden="true" size={14} /> {mapInfo?.available ? "Installed game map" : "Measured grid"}</span>
        <span className="canvas-route-readout"><Route aria-hidden="true" size={14} /> {workspace.belts.count} belts · {workspace.pipes.count} pipes</span>
      </div>
      <div className="canvas-corner canvas-corner--right">
        <span className="north-indicator"><Navigation aria-hidden="true" size={14} /> N</span>
        <Button type="button" size="sm" variant="outline" onClick={() => setView(fitView(platform))}>
          <Focus aria-hidden="true" size={15} /> Fit platform
        </Button>
      </div>
      {selectedMachine && (
        <aside className="machine-inspector" aria-live="polite">
          <div><ScanSearch aria-hidden="true" size={15} /><strong>{selectedMachine.name}</strong></div>
          <span>{selectedMachine.recipe_name ?? "No recipe"}</span>
          <dl>
            <div><dt>State</dt><dd>{selectedMachine.paused ? "paused" : selectedMachine.state}</dd></div>
            <div><dt>Clock</dt><dd>{Math.round((selectedMachine.clock ?? 1) * 100)}%</dd></div>
            <div><dt>Position</dt><dd>{selectedMachine.x_m?.toFixed(1)}, {selectedMachine.y_m?.toFixed(1)}</dd></div>
          </dl>
        </aside>
      )}
    </div>
  );
}
