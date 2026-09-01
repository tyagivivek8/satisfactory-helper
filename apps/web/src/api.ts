import type { AgentProvider, MapInfo, StatusResponse, StreamEvent, Workspace } from "./types";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function getStatus(): Promise<StatusResponse> {
  return fetchJson("/api/status");
}

export function getWorkspace(): Promise<Workspace> {
  return fetchJson("/api/workspace");
}

export function getMapInfo(): Promise<MapInfo> {
  return fetchJson("/api/map");
}

export interface ChatPayload {
  message: string;
  save_token: string;
  selected_factory: string | null;
  selected_floor: number | null;
  selected_site: Record<string, string | number> | null;
  conversation: Array<{ role: string; content: string }>;
  provider: AgentProvider;
  model: string | null;
}

export async function streamPlan(
  payload: ChatPayload,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `${response.status} ${response.statusText}`);
  }
  if (!response.body) throw new Error("The planning stream did not open.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  while (true) {
    const { done, value } = await reader.read();
    pending += decoder.decode(value, { stream: !done });
    const lines = pending.split("\n");
    pending = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as StreamEvent);
    }
    if (done) break;
  }
  if (pending.trim()) onEvent(JSON.parse(pending) as StreamEvent);
}
