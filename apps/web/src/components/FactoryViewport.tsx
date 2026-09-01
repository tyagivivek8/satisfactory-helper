import { Box, Map as MapIcon } from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { FactoryCanvas, type FactoryCanvasProps } from "./FactoryCanvas";
import { Button } from "./ui/button";

type ViewMode = "2d" | "3d";

const FactoryScene3D = lazy(async () => {
  const module = await import("./FactoryScene3D");
  return { default: module.FactoryScene3D };
});

const STORAGE_KEY = "satisfactory-helper-map-view";

function initialViewMode(): ViewMode {
  if (typeof window === "undefined") return "3d";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "2d" || stored === "3d" ? stored : "3d";
}

export function FactoryViewport(props: FactoryCanvasProps) {
  const [mode, setMode] = useState<ViewMode>(initialViewMode);

  function selectMode(nextMode: ViewMode) {
    setMode(nextMode);
    window.localStorage.setItem(STORAGE_KEY, nextMode);
  }

  return (
    <div className="factory-viewport">
      {mode === "2d" ? (
        <FactoryCanvas {...props} />
      ) : (
        <Suspense
          fallback={(
            <div className="scene-loading" aria-busy="true">
              <Box aria-hidden="true" size={18} />
              <span>Building the measured 3D scene…</span>
            </div>
          )}
        >
          <FactoryScene3D {...props} />
        </Suspense>
      )}

      <div className="view-mode-switch" role="group" aria-label="Factory map view">
        <Button
          type="button"
          size="sm"
          variant={mode === "2d" ? "default" : "ghost"}
          className={mode === "2d" ? "is-active" : ""}
          aria-pressed={mode === "2d"}
          onClick={() => selectMode("2d")}
        >
          <MapIcon aria-hidden="true" size={13} /> 2D
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mode === "3d" ? "default" : "ghost"}
          className={mode === "3d" ? "is-active" : ""}
          aria-pressed={mode === "3d"}
          onClick={() => selectMode("3d")}
        >
          <Box aria-hidden="true" size={13} /> 3D
        </Button>
      </div>
    </div>
  );
}
