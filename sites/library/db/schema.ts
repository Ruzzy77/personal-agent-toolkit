import { index, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const documents = sqliteTable(
  "documents",
  {
    id: text("id").primaryKey(),
    collection: text("collection", { enum: ["daily", "digest", "research"] }).notNull(),
    date: text("date").notNull(),
    publishedAt: text("published_at").notNull(),
    title: text("title").notNull(),
    referencesJson: text("references_json").notNull().default("[]"),
    canonicalPath: text("canonical_path").notNull(),
    textContent: text("text_content").notNull(),
    sourceHtml: text("source_html").notNull(),
    coverPath: text("cover_path"),
    updatedAt: text("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("documents_canonical_path_unique").on(table.canonicalPath),
    index("documents_published_at_idx").on(table.publishedAt),
    index("documents_collection_published_at_idx").on(table.collection, table.publishedAt),
  ],
);
