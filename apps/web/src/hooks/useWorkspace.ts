import { useCallback, useEffect, useRef, useState } from "react";
import { getMapInfo, getStatus, getWorkspace } from "../api";
import type { MapInfo, StatusResponse, Workspace } from "../types";

export function useWorkspace() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [mapInfo, setMapInfo] = useState<MapInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const generation = useRef<number | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [nextStatus, nextWorkspace, nextMap] = await Promise.all([
        getStatus(),
        getWorkspace(),
        getMapInfo().catch(() => null),
      ]);
      generation.current = nextStatus.generation;
      setStatus(nextStatus);
      setWorkspace(nextWorkspace);
      setMapInfo(nextMap);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        const nextStatus = await getStatus();
        setStatus(nextStatus);
        if (generation.current !== null && generation.current !== nextStatus.generation) {
          const nextWorkspace = await getWorkspace();
          generation.current = nextStatus.generation;
          setWorkspace(nextWorkspace);
        }
      } catch {
        // A transient status miss should not discard a usable workspace.
      }
    }, 4_000);
    return () => window.clearInterval(timer);
  }, []);

  return { status, workspace, mapInfo, error, loading, reload };
}
