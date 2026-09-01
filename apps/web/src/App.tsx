import { AlertTriangle, Bot, Box, Cpu, Layers3 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatPayload } from "./api";
import { ChatPanel, type ConversationMessage } from "./components/ChatPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { FactoryViewport } from "./components/FactoryViewport";
import { FloorRail } from "./components/FloorRail";
import { PlanInspector } from "./components/PlanInspector";
import { StatusRail } from "./components/StatusRail";
import { Button } from "./components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import { usePlanStream } from "./hooks/usePlanStream";
import { useWorkspace } from "./hooks/useWorkspace";
import type { AgentProvider, Machine, PlanAction, PlanAnswer } from "./types";

const AUTO_FACTORY_VALUE = "__auto_factory__";
const DEFAULT_MODEL_VALUE = "__provider_default__";

function platformMachineCount(platform: { bands: Array<{ machine_count: number }> }) {
  return platform.bands.reduce((total, band) => total + band.machine_count, 0);
}

export function App() {
  const { status, workspace, mapInfo, error: workspaceError, loading, reload } = useWorkspace();
  const { plan, events, running, error: planError, run, cancel } = usePlanStream();
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [selectedPlatform, setSelectedPlatform] = useState<number | null>(null);
  const [selectedFloor, setSelectedFloor] = useState<number | null>(null);
  const [selectedFactory, setSelectedFactory] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<PlanAction | null>(null);
  const [selectedMachine, setSelectedMachine] = useState<Machine | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<AgentProvider>("codex");
  const [selectedModel, setSelectedModel] = useState("");
  const answeredPlan = useRef<PlanAnswer | null>(null);

  const sortedPlatforms = useMemo(
    () =>
      [...(workspace?.floors.platforms ?? [])].sort(
        (left, right) => platformMachineCount(right) - platformMachineCount(left),
      ),
    [workspace?.floors.platforms],
  );
  const platform =
    workspace?.floors.platforms.find((candidate) => candidate.index === selectedPlatform) ??
    sortedPlatforms[0] ??
    null;
  const providerStatus = status?.providers[selectedProvider];

  useEffect(() => {
    if (selectedPlatform === null && sortedPlatforms[0]) setSelectedPlatform(sortedPlatforms[0].index);
  }, [selectedPlatform, sortedPlatforms]);

  useEffect(() => {
    if (providerStatus && !selectedModel) setSelectedModel(providerStatus.model);
  }, [providerStatus, selectedModel]);

  useEffect(() => {
    if (!plan || answeredPlan.current === plan) return;
    answeredPlan.current = plan;
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "assistant", content: `${plan.headline} ${plan.summary}` },
    ]);
    setActiveAction(plan.actions[0] ?? null);
  }, [plan]);

  function send(message: string) {
    if (!workspace) return;
    const nextMessage: ConversationMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: message,
    };
    const conversation = [...messages, nextMessage]
      .slice(-12)
      .map(({ role, content }) => ({ role, content }));
    setMessages((current) => [...current, nextMessage]);
    const payload: ChatPayload = {
      message,
      save_token: workspace.summary.save_token,
      selected_factory: selectedFactory,
      selected_floor: selectedFloor,
      selected_site:
        platform?.centre_m[0] != null && platform.centre_m[1] != null
          ? { x_m: platform.centre_m[0], y_m: platform.centre_m[1], platform: platform.index }
          : null,
      conversation,
      provider: selectedProvider,
      model: selectedModel || null,
    };
    void run(payload);
  }

  function selectAction(action: PlanAction) {
    setActiveAction(action);
    const floor = action.to_floor ?? action.from_floor;
    if (floor !== null) setSelectedFloor(floor);
  }

  if (loading) {
    return (
      <main className="boot-screen" aria-busy="true">
        <div className="boot-mark">SH</div>
        <p>Snapshotting the newest autosave…</p>
        <div className="boot-line"><span /></div>
      </main>
    );
  }

  if (!workspace || workspaceError) {
    return (
      <main className="blocked-screen" role="alert">
        <AlertTriangle aria-hidden="true" size={30} />
        <p className="instrument-label">Workbench blocked</p>
        <h1>Factory data is not ready.</h1>
        <p>{workspaceError ?? "No readable autosave snapshot was found."}</p>
        <Button type="button" onClick={() => void reload()}>Try again</Button>
        {status?.warnings.map((warning) => <pre key={warning}>{warning}</pre>)}
      </main>
    );
  }

  return (
    <ErrorBoundary>
      <div className="app-shell">
        <StatusRail status={status} workspace={workspace} refreshing={loading} onRefresh={() => void reload()} />
        <main className="workbench">
          <ChatPanel
            messages={messages}
            events={events}
            running={running}
            disabled={!providerStatus?.ready}
            error={planError}
            onSend={send}
            onCancel={cancel}
          />

          <section className="factory-workspace" aria-label="Current factory layout and proposed changes">
            <div className="workspace-toolbar" aria-label="Planning context">
              <div className="toolbar-context toolbar-context--agent">
                <span><Bot aria-hidden="true" size={15} /> Agent</span>
                <Select
                  value={selectedProvider}
                  disabled={running}
                  onValueChange={(value) => {
                    const provider = value as AgentProvider;
                    setSelectedProvider(provider);
                    setSelectedModel(status?.providers[provider].model ?? "");
                  }}
                >
                  <SelectTrigger size="sm" aria-label="Planning agent"><SelectValue /></SelectTrigger>
                  <SelectContent position="popper" align="start">
                    {(["codex", "claude"] as const).map((provider) => (
                      <SelectItem key={provider} value={provider}>
                        {status?.providers[provider].label ?? provider}
                        {status?.providers[provider].ready ? "" : " (offline)"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="toolbar-context toolbar-context--model">
                <span><Cpu aria-hidden="true" size={15} /> Model</span>
                <Select
                  value={selectedModel || DEFAULT_MODEL_VALUE}
                  disabled={running || !providerStatus?.ready}
                  onValueChange={(value) => setSelectedModel(value === DEFAULT_MODEL_VALUE ? "" : value)}
                >
                  <SelectTrigger size="sm" aria-label="Agent model"><SelectValue /></SelectTrigger>
                  <SelectContent position="popper" align="start">
                    {(providerStatus?.models ?? []).map((model) => (
                      <SelectItem key={model.id || "default"} value={model.id || DEFAULT_MODEL_VALUE}>
                        {model.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="toolbar-context toolbar-context--site">
                <span><Box aria-hidden="true" size={15} /> Site</span>
                <Select
                  value={selectedFactory ?? AUTO_FACTORY_VALUE}
                  onValueChange={(value) => setSelectedFactory(value === AUTO_FACTORY_VALUE ? null : value)}
                >
                  <SelectTrigger size="sm" aria-label="Factory context"><SelectValue /></SelectTrigger>
                  <SelectContent position="popper" align="start">
                    <SelectItem value={AUTO_FACTORY_VALUE}>Auto-detect from request</SelectItem>
                    {workspace.factories.labels.map((factory) => (
                      <SelectItem key={factory.name} value={factory.name}>{factory.name}</SelectItem>
                    ))}
                    {workspace.factories.proposals.map((proposal) => (
                      <SelectItem key={proposal.index} value={`proposal:${proposal.index}`}>
                        {proposal.label} ({proposal.machines} machines)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="toolbar-context toolbar-context--platform">
                <span><Layers3 aria-hidden="true" size={15} /> Platform</span>
                <Select
                  value={platform ? String(platform.index) : undefined}
                  disabled={!platform}
                  onValueChange={(value) => {
                    setSelectedPlatform(Number(value));
                    setSelectedFloor(null);
                  }}
                >
                  <SelectTrigger size="sm" aria-label="Platform"><SelectValue /></SelectTrigger>
                  <SelectContent position="popper" align="end">
                    {sortedPlatforms.map((candidate) => (
                      <SelectItem key={candidate.index} value={String(candidate.index)}>
                        Platform {candidate.index} ({platformMachineCount(candidate)} machines, {Math.round(candidate.area_m2)} m²)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="layout-stage">
              <FloorRail
                platform={platform}
                selectedFloor={selectedFloor}
                storage={workspace.storage.storage}
                onSelectFloor={setSelectedFloor}
              />
              <FactoryViewport
                workspace={workspace}
                mapInfo={mapInfo}
                platform={platform}
                selectedFloor={selectedFloor}
                plan={plan}
                activeAction={activeAction}
                selectedMachine={selectedMachine}
                onSelectMachine={setSelectedMachine}
              />
            </div>
            <PlanInspector plan={plan} activeAction={activeAction} onSelectAction={selectAction} />
          </section>
        </main>
      </div>
    </ErrorBoundary>
  );
}
