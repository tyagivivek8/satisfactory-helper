import { Layers3 } from "lucide-react";
import type { FloorBand, FloorPlatform, StoragePlacement } from "../types";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

interface Props {
  platform: FloorPlatform | null;
  selectedFloor: number | null;
  storage: StoragePlacement[];
  onSelectFloor: (floor: number | null) => void;
}

function storageOnBand(
  platform: FloorPlatform,
  band: FloorBand,
  storage: StoragePlacement[],
): number {
  if (band.top_m === null) return 0;
  return storage.filter(
    (placement) =>
      placement.platform === platform.index && placement.global_floor === band.ordinal,
  ).length;
}

export function FloorRail({ platform, selectedFloor, storage, onSelectFloor }: Props) {
  const usefulBands = platform?.bands
    .map((band) => ({ band, storageCount: storageOnBand(platform, band, storage) }))
    .filter(({ band, storageCount }) => !band.minor || band.machine_count > 0 || storageCount > 0);

  return (
    <nav className="floor-rail" aria-label="Factory floors">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            className={selectedFloor === null ? "is-selected" : undefined}
            aria-pressed={selectedFloor === null}
            onClick={() => onSelectFloor(null)}
          >
            <Layers3 aria-hidden="true" size={15} />
            All
          </Button>
        </TooltipTrigger>
        <TooltipContent side="right">Show the whole platform</TooltipContent>
      </Tooltip>
      {usefulBands?.map(({ band, storageCount }) => (
        <Tooltip key={band.ordinal}>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className={selectedFloor === band.ordinal ? "is-selected" : undefined}
              aria-label={`Floor ${band.ordinal}, ${band.machine_count} machines, ${storageCount} storage`}
              aria-pressed={selectedFloor === band.ordinal}
              onClick={() => onSelectFloor(band.ordinal)}
            >
              <span>F{band.ordinal}</span>
              <small>{band.machine_count + storageCount}</small>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            Floor {band.ordinal} · {band.top_m?.toFixed(1) ?? "?"} m · {band.machine_count} machines · {storageCount} storage
          </TooltipContent>
        </Tooltip>
      ))}
    </nav>
  );
}
