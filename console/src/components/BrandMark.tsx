import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

type BrandMarkProps = {
  className?: string;
  iconClassName?: string;
  showName?: boolean;
};

export function BrandMark({ className, iconClassName, showName = true }: BrandMarkProps) {
  return (
    <Link to="/" className={cn("inline-flex items-center gap-2.5", className)}>
      <img
        src="/brand/icon.png"
        alt={showName ? "" : "MeteorEdge"}
        className={cn("h-8 w-8 shrink-0", iconClassName)}
      />
      {showName && (
        <span className="text-lg font-semibold leading-none tracking-tight text-foreground">
          Meteor<span className="text-link">Edge</span>
        </span>
      )}
    </Link>
  );
}
