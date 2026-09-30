import { useState } from "react";

import { Button } from "@/components/ui/button";

type OneTimeSecretDialogProps = {
  title: string;
  description: string;
  secret: string;
  /** Optional shell command to copy (e.g. the agent register command). */
  command?: string;
  onClose: () => void;
};

async function copyToClipboard(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function OneTimeSecretDialog({
  title,
  description,
  secret,
  command,
  onClose,
}: OneTimeSecretDialogProps) {
  const [copied, setCopied] = useState<string | null>(null);

  async function onCopy(label: string, value: string) {
    if (await copyToClipboard(value)) {
      setCopied(label);
      window.setTimeout(() => setCopied(null), 2000);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="w-full max-w-lg space-y-4 rounded-lg border border-border bg-white p-6 shadow-lg">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Secret (shown once)
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-border bg-secondary/60 px-3 py-2 text-sm">
              {secret}
            </code>
            <Button type="button" variant="secondary" size="sm" onClick={() => onCopy("secret", secret)}>
              {copied === "secret" ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>

        {command && (
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Register command
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto whitespace-pre rounded-md border border-border bg-secondary/60 px-3 py-2 text-xs">
                {command}
              </code>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onCopy("command", command)}
              >
                {copied === "command" ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Tip: prefer <code>--token-file</code> so the secret is not stored in shell history.
            </p>
          </div>
        )}

        <div className="flex justify-end">
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}
