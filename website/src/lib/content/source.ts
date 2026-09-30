import { filesystemSource } from "./filesystem";
import type { ContentSource } from "./types";

export function getContentSource(): ContentSource {
  const provider = (process.env.WEBSITE_CONTENT_SOURCE ?? "filesystem").trim().toLowerCase();
  if (provider === "filesystem") {
    return filesystemSource;
  }
  if (provider === "database") {
    throw new Error(
      "WEBSITE_CONTENT_SOURCE=database is reserved. Use a database owned by the website, not the control-plane Postgres.",
    );
  }
  throw new Error(`Unsupported WEBSITE_CONTENT_SOURCE=${provider}; use filesystem`);
}
