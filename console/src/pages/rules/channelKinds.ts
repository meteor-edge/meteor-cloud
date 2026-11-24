import type { IntegrationKind } from "@/api/rules";

export const KIND_LABEL: Record<IntegrationKind, string> = {
  email: "Email",
  telegram: "Telegram",
  slack: "Slack",
  webhook: "Webhook",
};
