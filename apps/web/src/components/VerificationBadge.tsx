import { CircleCheck, OctagonX, TriangleAlert } from "lucide-react";
import type { VerificationStatus } from "../types";
import { Badge } from "./ui/badge";

const copy: Record<VerificationStatus, string> = {
  verified: "Verified",
  needs_confirmation: "Needs confirmation",
  blocked: "Blocked",
};

export function VerificationBadge({ status }: { status: VerificationStatus }) {
  const Icon =
    status === "verified" ? CircleCheck : status === "blocked" ? OctagonX : TriangleAlert;
  return (
    <Badge variant="outline" className={`verification verification--${status}`}>
      <Icon aria-hidden="true" size={13} strokeWidth={2} />
      {copy[status]}
    </Badge>
  );
}
