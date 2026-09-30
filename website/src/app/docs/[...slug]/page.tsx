import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DocsNav } from "@/components/DocsNav";
import { Markdown } from "@/components/Markdown";
import { getContentSource } from "@/lib/content/source";
import { site } from "@/lib/site";

type Params = { slug: string[] };

export async function generateStaticParams() {
  const docs = await getContentSource().listDocs();
  return docs.map((doc) => ({ slug: [doc.slug] }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getContentSource().getDoc(slug.join("/"));
  if (!doc) {
    return { title: "Not found" };
  }
  return {
    title: doc.title,
    description: doc.description,
    openGraph: {
      title: `${doc.title} · ${site.name}`,
      description: doc.description,
      type: "article",
    },
  };
}

export default async function DocPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const source = getContentSource();
  const [docs, doc] = await Promise.all([source.listDocs(), source.getDoc(slug.join("/"))]);
  if (!doc) {
    notFound();
  }
  return (
    <div className="grid gap-10 lg:grid-cols-[220px_1fr]">
      <aside>
        <DocsNav docs={docs} active={doc.slug} />
      </aside>
      <article>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">{doc.section}</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight">{doc.title}</h1>
        <p className="mt-2 text-muted">{doc.description}</p>
        <div className="mt-8">
          <Markdown source={doc.body} />
        </div>
      </article>
    </div>
  );
}
