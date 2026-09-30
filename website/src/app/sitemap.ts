import type { MetadataRoute } from "next";

import { getContentSource } from "@/lib/content/source";
import { site } from "@/lib/site";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const docs = await getContentSource().listDocs();
  const now = new Date();
  const staticPages = ["", "/about", "/contact", "/docs"].map((path) => ({
    url: `${site.url}${path || "/"}`,
    lastModified: now,
    changeFrequency: "weekly" as const,
    priority: path === "" ? 1 : 0.8,
  }));
  const docPages = docs.map((doc) => ({
    url: `${site.url}/docs/${doc.slug}`,
    lastModified: now,
    changeFrequency: "weekly" as const,
    priority: 0.7,
  }));
  return [...staticPages, ...docPages];
}
