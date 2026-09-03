import { LibraryError } from "./errors";
import {
  createIssue,
  importIssue,
  isCollection,
  listIssues,
  readIssue,
  readIssueByPath,
  updateIssueFragments,
  updateIssueSource,
} from "./repository";
import type {
  LibraryCreateInput,
  LibraryFragmentUpdateInput,
  LibraryImportInput,
  LibraryUpdateInput,
} from "./schemas";
import type {
  Env,
  LibraryAssetResult,
  LibraryCollection,
  LibraryIssue,
  LibraryIssueSummary,
  LibraryMutationResult,
} from "./types";

const MAX_ASSET_BYTES = 10 * 1024 * 1024;
const ALLOWED_ASSET_TYPES = new Set([
  "image/avif",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

export function assetKey(value: string): string {
  if (
    !value
    || value.startsWith("/")
    || value.includes("..")
    || !/^[a-zA-Z0-9/_-]+\.[a-zA-Z0-9]+$/.test(value)
  ) {
    throw new LibraryError("invalid_asset_path", "asset path is invalid");
  }
  return value;
}

export class LibraryService {
  constructor(private readonly env: Pick<Env, "DB" | "MEDIA">) {}

  listIssues(
    collection: string | null,
    limit: number,
  ): Promise<LibraryIssueSummary[]> {
    if (collection !== null && !isCollection(collection)) {
      throw new LibraryError("invalid_collection", "collection is invalid");
    }
    return listIssues(this.env.DB, {
      collection: collection as LibraryCollection | null,
      limit,
    });
  }

  readIssue(id: string): Promise<LibraryIssue | null> {
    return readIssue(this.env.DB, id);
  }

  readIssueByPath(path: string): Promise<LibraryIssue | null> {
    return readIssueByPath(this.env.DB, path);
  }

  createIssue(input: LibraryCreateInput): Promise<LibraryMutationResult> {
    return createIssue(this.env.DB, {
      id: input.id,
      sourceHtml: input.source_html,
      ...(input.published_at === undefined
        ? {}
        : { publishedAt: input.published_at }),
      ...(input.references === undefined ? {} : { references: input.references }),
      ...(input.cover_path === undefined ? {} : { coverPath: input.cover_path }),
    });
  }

  importIssue(input: LibraryImportInput): Promise<LibraryMutationResult> {
    return importIssue(this.env.DB, {
      id: input.id,
      collection: input.collection,
      date: input.date,
      publishedAt: input.published_at,
      title: input.title,
      references: input.references,
      canonicalPath: input.canonical_path,
      text: input.text,
      sourceHtml: input.source_html,
      coverPath: input.cover_path,
      updatedAt: input.updated_at,
    });
  }

  updateIssue(
    id: string,
    input: LibraryUpdateInput,
  ): Promise<LibraryMutationResult> {
    return updateIssueSource(this.env.DB, id, {
      sourceHtml: input.source_html,
      expectedVersion: input.expected_version,
      ...(input.cover_path === undefined ? {} : { coverPath: input.cover_path }),
      ...(input.references === undefined ? {} : { references: input.references }),
    });
  }

  updateIssueFragments(
    id: string,
    input: LibraryFragmentUpdateInput,
  ): Promise<LibraryMutationResult> {
    return updateIssueFragments(this.env.DB, id, {
      title: input.title,
      articleHtml: input.article_html,
      expectedVersion: input.expected_version,
      ...(input.lead_text === undefined ? {} : { leadText: input.lead_text }),
    });
  }

  async uploadAsset(
    path: string,
    contentType: string,
    bytes: Uint8Array,
  ): Promise<LibraryAssetResult> {
    const key = assetKey(path);
    if (!ALLOWED_ASSET_TYPES.has(contentType)) {
      throw new LibraryError("invalid_asset_type", "asset type is not supported");
    }
    if (bytes.byteLength < 1 || bytes.byteLength > MAX_ASSET_BYTES) {
      throw new LibraryError("invalid_asset_size", "asset size is invalid");
    }
    await this.env.MEDIA.put(key, bytes, { httpMetadata: { contentType } });
    return { status: "stored", path: `/media/${key}`, bytes: bytes.byteLength };
  }

  async readAsset(path: string): Promise<R2ObjectBody | null> {
    return this.env.MEDIA.get(assetKey(path));
  }
}
