"use client";

import { FormEvent, useState } from "react";

export function ContactForm() {
  const [status, setStatus] = useState<"idle" | "sending" | "ok" | "error">("idle");
  const [message, setMessage] = useState("");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("sending");
    const form = new FormData(event.currentTarget);
    const body = {
      name: String(form.get("name") ?? ""),
      email: String(form.get("email") ?? ""),
      company: String(form.get("company") ?? ""),
      message: String(form.get("message") ?? ""),
    };
    try {
      const response = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error("request failed");
      }
      setStatus("ok");
      setMessage("Thanks. We received your message.");
      event.currentTarget.reset();
    } catch {
      setStatus("error");
      setMessage("Could not send just now. Email us using the address on this page.");
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-8 max-w-lg space-y-4">
      <label className="block text-sm font-medium">
        Name
        <input
          required
          name="name"
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2"
          autoComplete="name"
        />
      </label>
      <label className="block text-sm font-medium">
        Email
        <input
          required
          type="email"
          name="email"
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2"
          autoComplete="email"
        />
      </label>
      <label className="block text-sm font-medium">
        Company
        <input name="company" className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2" />
      </label>
      <label className="block text-sm font-medium">
        Message
        <textarea
          required
          name="message"
          rows={5}
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2"
        />
      </label>
      <button
        type="submit"
        disabled={status === "sending"}
        className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-dark disabled:opacity-60"
      >
        {status === "sending" ? "Sending…" : "Send"}
      </button>
      {message ? <p className="text-sm text-muted">{message}</p> : null}
    </form>
  );
}
