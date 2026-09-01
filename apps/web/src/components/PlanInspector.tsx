import {
  ArrowDownUp,
  Cable,
  ChevronDown,
  ChevronRight,
  CircleMinus,
  CirclePlus,
  Gauge,
  ListChecks,
  ScanLine,
  Settings2,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import type { PlanAction, PlanAnswer } from "../types";
import { UnlockAdvice } from "./UnlockAdvice";
import { VerificationBadge } from "./VerificationBadge";
import { Button } from "./ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import { ScrollArea } from "./ui/scroll-area";

interface Props {
  plan: PlanAnswer | null;
  activeAction: PlanAction | null;
  onSelectAction: (action: PlanAction) => void;
}

const actionIcons = {
  add: CirclePlus,
  remove: CircleMinus,
  move: ArrowDownUp,
  reroute: Cable,
  set_recipe: Settings2,
  change_clock: Gauge,
  keep: ListChecks,
  manual_check: ScanLine,
};

function PlanSummary({ summary }: { summary: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible className="plan-summary" open={open} onOpenChange={setOpen}>
      {!open && <p className="plan-summary-preview">{summary}</p>}
      <CollapsibleTrigger asChild>
        <Button className="plan-summary-toggle" type="button" variant="ghost" size="xs">
          {open ? "Hide reasoning" : "Show full reasoning"}
          <ChevronDown aria-hidden="true" className={open ? "is-open" : undefined} size={14} />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <p className="plan-summary-full">{summary}</p>
      </CollapsibleContent>
    </Collapsible>
  );
}

function PlanDisclosure({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible className="plan-disclosure" open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost">
          <span>{label}</span>
          <ChevronDown aria-hidden="true" className={open ? "is-open" : undefined} size={15} />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="plan-disclosure-content">{children}</CollapsibleContent>
    </Collapsible>
  );
}

export function PlanInspector({ plan, activeAction, onSelectAction }: Props) {
  if (!plan) {
    return (
      <section className="plan-inspector plan-inspector--empty">
        <div>
          <p className="instrument-label">Change set</p>
          <h2>No proposed changes yet</h2>
          <p>
            Ask for an output at a site. Verified machine math and inferred floor moves
            will appear here with clear confidence labels.
          </p>
        </div>
        <div className="change-grammar" aria-label="Plan change legend">
          <span className="change-add">Add</span>
          <span className="change-move">Move</span>
          <span className="change-remove">Remove</span>
          <span className="change-reroute">Reroute</span>
        </div>
      </section>
    );
  }

  return (
    <section className="plan-inspector">
      <header className="plan-outcome">
        <div>
          <VerificationBadge status={plan.overall_status} />
          <h2>{plan.headline}</h2>
          <PlanSummary summary={plan.summary} />
        </div>
        <div className="metric-strip">
          {plan.metrics.slice(0, 5).map((metric) => (
            <div key={metric.label}>
              <span>{metric.label}</span>
              <strong>
                {metric.value}
                {metric.unit ? <small> {metric.unit}</small> : null}
              </strong>
            </div>
          ))}
        </div>
      </header>

      <div className="plan-body">
        <ScrollArea className="action-list">
          <div className="section-title">
            <span>Ordered changes</span>
            <small>{plan.actions.length} actions</small>
          </div>
          {plan.actions.map((action, index) => {
            const Icon = actionIcons[action.kind];
            return (
              <Button
                type="button"
                variant="ghost"
                key={action.id}
                className={`action-row action-row--${action.kind} ${activeAction?.id === action.id ? "is-active" : ""}`}
                aria-pressed={activeAction?.id === action.id}
                onClick={() => onSelectAction(action)}
              >
                <span className="action-index">{String(index + 1).padStart(2, "0")}</span>
                <Icon aria-hidden="true" size={17} />
                <span className="action-copy">
                  <strong>{action.title}</strong>
                  <small>
                    {[action.building, action.recipe, action.to_floor !== null ? `F${action.to_floor}` : action.site]
                      .filter(Boolean)
                      .join(" / ") || action.why}
                  </small>
                </span>
                <VerificationBadge status={action.status} />
                <ChevronRight aria-hidden="true" size={16} />
              </Button>
            );
          })}
          {!plan.actions.length && <p className="inline-empty">No physical changes are required.</p>}
        </ScrollArea>

        <ScrollArea className="plan-details">
          {activeAction ? (
            <div className="active-action">
              <p className="instrument-label">Selected action</p>
              <h3>{activeAction.title}</h3>
              <p>{activeAction.why}</p>
              <dl>
                {activeAction.quantity !== null && <div><dt>Quantity</dt><dd>{activeAction.quantity}</dd></div>}
                {activeAction.rate_per_min !== null && <div><dt>Rate</dt><dd>{activeAction.rate_per_min}/min</dd></div>}
                {activeAction.from_floor !== null && <div><dt>From</dt><dd>Floor {activeAction.from_floor}</dd></div>}
                {activeAction.to_floor !== null && <div><dt>To</dt><dd>Floor {activeAction.to_floor}</dd></div>}
                {activeAction.source_distance_m !== null && (
                  <div><dt>Raw route</dt><dd>{Math.round(activeAction.source_distance_m)} m</dd></div>
                )}
                {activeAction.transport_mode !== "none" && (
                  <div><dt>Transport</dt><dd>{activeAction.transport_mode}</dd></div>
                )}
              </dl>
              {activeAction.connections.length > 0 && (
                <ul>{activeAction.connections.map((connection) => <li key={connection}>{connection}</li>)}</ul>
              )}
            </div>
          ) : (
            <div className="floor-plan-summary">
              <p className="instrument-label">Floor sequence</p>
              {plan.floors.map((floor) => (
                <article key={`${floor.floor}-${floor.label}`}>
                  <strong>{floor.floor === null ? floor.label : `F${floor.floor} · ${floor.label}`}</strong>
                  <span>{floor.instructions.length} steps</span>
                </article>
              ))}
            </div>
          )}

          {(plan.assumptions.length > 0 || plan.blockers.length > 0) && (
            <PlanDisclosure label="Assumptions & blockers">
              <ul>
                {plan.blockers.map((item) => <li key={item} className="blocker">{item}</li>)}
                {plan.assumptions.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </PlanDisclosure>
          )}
          {plan.raw_inputs.length > 0 && (
            <PlanDisclosure label="Raw supply">
              <ul>
                {plan.raw_inputs.map((input) => (
                  <li key={input.item}>
                    {input.item}: {input.rate_per_min}/min via {input.strategy.replaceAll("_", " ")}. {input.effect}
                  </li>
                ))}
              </ul>
            </PlanDisclosure>
          )}
          <UnlockAdvice advice={plan.unlock_advice} />
        </ScrollArea>
      </div>
    </section>
  );
}
