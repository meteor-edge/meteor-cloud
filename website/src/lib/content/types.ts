/**
 * Content is loaded through this port so a later CMS can use a dedicated
 * database (not the control-plane Postgres). Select with WEBSITE_CONTENT_SOURCE.
 */
export type DocSection = "Overview" | "Platform" | "Reference" | "Operations";

export type ContentDocument = {
  slug: string;
  title: string;
  description: string;
  section: DocSection;
  order: number;
  body: string;
};

export type ContentSource = {
  listDocs(): Promise<ContentDocument[]>;
  getDoc(slug: string): Promise<ContentDocument | null>;
};
