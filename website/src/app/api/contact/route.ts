import { NextResponse } from "next/server";

type ContactBody = {
  name?: string;
  email?: string;
  company?: string;
  message?: string;
};

export async function POST(request: Request) {
  let body: ContactBody;
  try {
    body = (await request.json()) as ContactBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const name = body.name?.trim() ?? "";
  const email = body.email?.trim() ?? "";
  const message = body.message?.trim() ?? "";
  if (name.length < 2 || !email.includes("@") || message.length < 10) {
    return NextResponse.json({ error: "invalid_fields" }, { status: 422 });
  }
  const webhook = process.env.CONTACT_WEBHOOK_URL?.trim();
  if (webhook) {
    const response = await fetch(webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        email,
        company: body.company?.trim() ?? "",
        message,
      }),
    });
    if (!response.ok) {
      return NextResponse.json({ error: "delivery_failed" }, { status: 502 });
    }
  }
  return NextResponse.json({ ok: true });
}
