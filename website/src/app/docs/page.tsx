import type { Metadata } from "next";
import Link from "next/link";

import { DocsNav } from "@/components/DocsNav";
import { getContentSource } from "@/lib/content/source";

export const metadata: Metadata = {
  title: "Docs",
  description: "MeteorCloud documentation: architecture, API, agent, and CLI.",
};

export default async function DocsIndexPage() {
  const docs = await getContentSource().listDocs();
  return (
    <div className="grid gap-10 lg:grid-cols-[220px_1fr]">
      <aside>
        <DocsNav docs={docs} />
      </aside>
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Documentation</h1>
        <p className="mt-3 max-w-2xl text-muted">
          Start with how the modules fit, then the API, the device agent, and meteorcli. Pages are Markdown
          files today; a later CMS can swap the content source to a website-owned database.
        </p>
        <ol className="mt-8 space-y-3">
          {docs.map((doc) => (
            <li key={doc.slug}>
              <Link href={`/docs/${doc.slug}`} className="font-medium text-primary-dark hover:underline">
                {doc.title}
              </Link>
              <p className="text-sm text-muted">{doc.description}</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
