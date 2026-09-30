import type { Metadata } from "next";

import { ContactForm } from "@/components/ContactForm";

export const metadata: Metadata = {
  title: "Contact",
  description: "Talk to MeteorCloud about self-hosted fleet control.",
};

export default function ContactPage() {
  return (
    <article className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight">Contact</h1>
      <p className="mt-4 text-muted">
        Questions about deploying the control plane, MQTT, or the agent: send a short note. We do not use the
        fleet Postgres for this form.
      </p>
      <ContactForm />
    </article>
  );
}
