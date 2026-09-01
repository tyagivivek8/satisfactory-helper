import { Disc3, FlaskConical, Milestone } from "lucide-react";
import type { PlanAnswer } from "../types";
import { Badge } from "./ui/badge";

export function UnlockAdvice({ advice }: { advice: PlanAnswer["unlock_advice"] }) {
  if (!advice.length || advice.every((item) => item.kind === "none")) return null;
  return (
    <section className="unlock-advice">
      <div className="section-title">
        <span>Useful next unlocks</span>
        <small>Not used by the current plan</small>
      </div>
      {advice.map((item) => {
        const Icon = item.kind === "hard_drive" ? Disc3 : item.kind === "mam" ? FlaskConical : Milestone;
        return (
          <article key={`${item.kind}-${item.name}`}>
            <Icon aria-hidden="true" size={15} />
            <div>
              <strong>{item.name}</strong>
              <p>{item.reason}</p>
            </div>
            <Badge variant="outline" className={`priority priority--${item.priority}`}>{item.priority}</Badge>
          </article>
        );
      })}
    </section>
  );
}
