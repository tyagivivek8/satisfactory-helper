import { Bot, Database, HardDrive, LockKeyhole, RefreshCw } from "lucide-react";
import type { StatusResponse, Workspace } from "../types";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

interface Props {
  status: StatusResponse | null;
  workspace: Workspace | null;
  refreshing: boolean;
  onRefresh: () => void;
}

export function StatusRail({ status, workspace, refreshing, onRefresh }: Props) {
  const build = workspace?.summary.header.build_version;
  const machineCount = workspace
    ? workspace.machines.machines.length +
      workspace.machines.extractors.length +
      workspace.machines.generators.length
    : null;
  const readyProviders = status
    ? Object.values(status.providers)
        .filter((provider) => provider.ready)
        .map((provider) => provider.label)
    : [];
  return (
    <header className="status-rail">
      <div className="wordmark" aria-label="Satisfactory Helper">
        <span className="wordmark-mark">SH</span>
        <span>Satisfactory Helper</span>
      </div>
      <div className="status-items" aria-label="Connection status">
        <span className={`status-item status-item--${status?.state ?? "loading"}`}>
          <Database aria-hidden="true" size={14} />
          {status?.save?.source_name ?? "Finding autosave"}
        </span>
        <span className="status-item">
          <HardDrive aria-hidden="true" size={14} />
          {build ? `Build ${build}` : "Game data"}
        </span>
        <span className="status-item">
          <Bot aria-hidden="true" size={14} />
          {readyProviders.length > 0 ? readyProviders.join(" + ") : "Agents offline"}
        </span>
        <span className="status-item status-item--safe" title="The parser only receives snapshots">
          <LockKeyhole aria-hidden="true" size={14} />
          Originals read-only
        </span>
        {machineCount !== null && <span className="status-item">{machineCount} actors</span>}
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            className="icon-button"
            size="icon"
            variant="ghost"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Refresh snapshot and workspace"
          >
            <RefreshCw aria-hidden="true" size={16} className={refreshing ? "spin" : undefined} />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">Refresh the safe snapshot</TooltipContent>
      </Tooltip>
    </header>
  );
}
