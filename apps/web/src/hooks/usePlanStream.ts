import { useCallback, useEffect, useRef, useState } from "react";
import { streamPlan, type ChatPayload } from "../api";
import type { PlanAnswer, StreamEvent } from "../types";

export function usePlanStream() {
  const [plan, setPlan] = useState<PlanAnswer | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => controller.current?.abort(), []);

  const run = useCallback(async (payload: ChatPayload) => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setRunning(true);
    setError(null);
    setEvents([]);
    try {
      await streamPlan(
        payload,
        (event) => {
          setEvents((current) => [...current.slice(-19), event]);
          if (event.type === "answer" && event.data) setPlan(event.data);
          if (event.type === "error") setError(event.message ?? "Planning failed.");
        },
        nextController.signal,
      );
    } catch (reason) {
      if (!nextController.signal.aborted) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (controller.current === nextController) controller.current = null;
      setRunning(false);
    }
  }, []);

  useEffect(() => cancel, [cancel]);

  return { plan, events, running, error, run, cancel, setPlan };
}
