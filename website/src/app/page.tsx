import Image from "next/image";
import Link from "next/link";

import { site } from "@/lib/site";

const modules = [
  {
    title: "Control plane",
    text: "Identity, organizations, device registry, enrollment, and MQTT policy. PostgreSQL last-value telemetry.",
  },
  {
    title: "Data plane",
    text: "EMQX session for the platform. Publish, subscribe, ingest to the control plane over HTTP.",
  },
  {
    title: "Console",
    text: "Operator UI for fleet and orgs. The browser talks only to the control-plane API.",
  },
  {
    title: "Device agent",
    text: "meteorcli on Linux: enroll, heartbeat, TLS MQTT. Two enrollment paths.",
  },
];

export default function HomePage() {
  return (
    <div>
      <section className="grid items-center gap-10 pb-16 lg:grid-cols-2">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-primary-dark">{site.tagline}</p>
          <h1 className="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">
            Run your Linux fleet on infrastructure you operate.
          </h1>
          <p className="mt-4 max-w-xl text-lg text-muted">{site.description}</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/docs/getting-started"
              className="rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white hover:bg-primary-dark"
            >
              Get started
            </Link>
            <Link
              href="/docs"
              className="rounded-lg border border-border bg-card px-5 py-2.5 text-sm font-semibold hover:border-primary"
            >
              Read the docs
            </Link>
          </div>
        </div>
        <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <Image src="/brand/banner.png" alt="MeteorCloud" width={1200} height={630} className="h-auto w-full" />
        </div>
      </section>
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Modules you can deploy on their own</h2>
        <p className="mt-2 max-w-2xl text-muted">
          Each plane is a process with its own Compose file. Join them on one host or point URLs across servers.
        </p>
        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {modules.map((item) => (
            <article key={item.title} className="rounded-xl border border-border bg-card p-5">
              <h3 className="font-semibold">{item.title}</h3>
              <p className="mt-2 text-sm text-muted">{item.text}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
