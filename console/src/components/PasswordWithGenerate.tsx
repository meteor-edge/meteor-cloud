import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { generatePassword } from "@/lib/generatePassword";
import { cn } from "@/lib/utils";

type Props = {
  id: string;
  value: string;
  onChange: (value: string) => void;
  /** Wi-Fi PSK max 63; SSH/login can use longer (caller validates). */
  length?: number;
  autoComplete?: string;
  className?: string;
  "aria-label"?: string;
};

/** Password input plus a Generate button that fills a random value. */
export function PasswordWithGenerate({
  id,
  value,
  onChange,
  length = 16,
  autoComplete = "new-password",
  className,
  "aria-label": ariaLabel,
}: Props) {
  return (
    <div className={cn("flex gap-2", className)}>
      <Input
        id={id}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        spellCheck={false}
        className="font-mono"
        aria-label={ariaLabel}
      />
      <Button
        type="button"
        variant="outline"
        className="shrink-0"
        onClick={() => onChange(generatePassword(length))}
        title="Generate a random password"
      >
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
        Generate
      </Button>
    </div>
  );
}
