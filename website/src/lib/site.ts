export const site = {
  name: "MeteorCloud",
  tagline: "Self-hosted Linux fleet control",
  description:
    "Run your own control plane, MQTT data plane, and operator console. Enroll Linux devices, stream status over TLS MQTT, and keep identity and policy on infrastructure you operate.",
  url: process.env.SITE_URL ?? "http://localhost:3000",
} as const;
