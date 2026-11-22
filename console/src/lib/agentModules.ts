/**
 * What each meteorcli module does, in a sentence for the console (from the Go packages under
 * src/device-plane/agent-go/internal). A module this list does not know shows no description.
 */
export const AGENT_MODULE_DESCRIPTIONS: Record<string, string> = {
  actions:
    "Runs one-off actions ordered from the console: restart, shut down, set a password, add a " +
    "user, update meteorcli, run diagnostics and restart a module. It reports each result back.",
  appcreds:
    "Keeps the app credentials (SDK tokens) of deployed apps fresh. It rotates them in place, so " +
    "a running app gets the new token without a restart.",
  deploy:
    "Installs the software this device should run: verifies signatures, downloads artifacts and " +
    "checks their SHA-256, applies them (Docker, Compose or systemd) and reports the result.",
  heartbeat:
    "Tells MeteorCloud the device is alive, with its inventory, capabilities and module health, " +
    "and learns from the answer which work waits (actions, deployments).",
  identity:
    "Owns who the device is: enrollment (registration token or approval), the device credential, " +
    "and the MQTT credentials and their refresh.",
  metrics:
    "Publishes the device's resource readings over MQTT every 60 seconds (the Live resources " +
    "card), plus app start/stop events and journal warnings.",
  mqtt:
    "Keeps the MQTT connection to MeteorCloud up, marks the device online, and answers commands " +
    "such as ping and sync.",
  socket:
    "The local socket the meteorcli commands on the device talk to (status, doctor, register, " +
    "update), so they work without sudo.",
  wifi:
    "Applies Wi-Fi changes ordered from the console and keeps them only when MeteorCloud is still " +
    "reachable on the new network.",
  wireguard:
    "Keeps the WireGuard tunnel to MeteorCloud up, and the SSH keys the console's browser terminal " +
    "uses to sign in.",
};

export function agentModuleDescription(name: string): string | null {
  return AGENT_MODULE_DESCRIPTIONS[name] ?? null;
}
