import type { ContentDocument, DocSection } from "@/lib/content/types";
import Link from "next/link";

const SECTION_ORDER: DocSection[] = ["Overview", "Platform", "Reference", "Operations"];

export function DocsNav({ docs, active }: { docs: ContentDocument[]; active?: string }) {
  const grouped = SECTION_ORDER.map((section) => ({
    section,
    items: docs.filter((doc) => doc.section === section),
  })).filter((group) => group.items.length > 0);

  return (
    <nav className="text-sm">
      {grouped.map((group) => (
        <div key={group.section} className="mb-6">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{group.section}</p>
          <ul className="space-y-1">
            {group.items.map((doc) => {
              const href = `/docs/${doc.slug}`;
              const on = active === doc.slug;
              return (
                <li key={doc.slug}>
                  <Link
                    href={href}
                    className={`block rounded-md px-2 py-1 ${on ? "bg-[rgb(16_102_240/0.12)] font-medium text-foreground" : "text-muted hover:text-foreground"}`}
                  >
                    {doc.title}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
