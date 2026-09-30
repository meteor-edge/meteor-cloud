import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "About",
  description: "MeteorCloud builds self-hosted software for Linux device fleets.",
};

export default function AboutPage() {
  return (
    <article className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight">About MeteorCloud</h1>
      <p className="mt-4 text-muted">
        MeteorCloud is software for operators who want a Linux fleet without handing identity, telemetry, and
        device policy to a public cloud IoT suite.
      </p>
      <p className="mt-4 text-muted">
        The control plane stays with you: organizations, RBAC, enrollment, and last-value device status. MQTT
        traffic is a separate data-plane process in front of EMQX. The operator console is a browser app that
        only calls the control-plane API. Devices run an open agent, meteorcli.
      </p>
      <p className="mt-4 text-muted">
        This website is a separate Next.js application. It does not log into your fleet. Documentation is
        shipped as files today; a later CMS can use a database owned by the website, not the control plane.
      </p>
    </article>
  );
}
