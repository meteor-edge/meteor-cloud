/** True when the agent reports a simulated board (docs/fleet/simulator.md). */
export function isSimulatedDevice(device: { metadata?: Record<string, unknown> | null }): boolean {
  return device.metadata?.simulated === true;
}
