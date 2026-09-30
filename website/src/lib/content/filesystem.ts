import fs from "node:fs/promises";
import path from "node:path";

import matter from "gray-matter";

import type { ContentDocument, ContentSource, DocSection } from "./types";

const DOCS_DIR = path.join(process.cwd(), "content", "docs");

function asSection(value: unknown): DocSection {
  const text = String(value ?? "Overview");
  if (text === "Platform" || text === "Reference" || text === "Operations" || text === "Overview") {
    return text;
  }
  return "Overview";
}

async function loadAll(): Promise<ContentDocument[]> {
  const names = await fs.readdir(DOCS_DIR);
  const docs: ContentDocument[] = [];
  for (const name of names) {
    if (!name.endsWith(".md")) continue;
    const raw = await fs.readFile(path.join(DOCS_DIR, name), "utf8");
    const parsed = matter(raw);
    const slug = name.replace(/\.md$/, "");
    docs.push({
      slug,
      title: String(parsed.data.title ?? slug),
      description: String(parsed.data.description ?? ""),
      section: asSection(parsed.data.section),
      order: Number(parsed.data.order ?? 99),
      body: parsed.content.trim(),
    });
  }
  return docs.sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
}

export const filesystemSource: ContentSource = {
  async listDocs() {
    return loadAll();
  },
  async getDoc(slug: string) {
    const all = await loadAll();
    return all.find((doc) => doc.slug === slug) ?? null;
  },
};
