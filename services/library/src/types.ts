import type { AuthServiceBinding } from "@personal-agent/remote-runtime";

export type {
  AccessValidationResult,
  AuthenticatedOwner,
  AuthServiceBinding,
} from "@personal-agent/remote-runtime";

export type LibraryCollection = "daily" | "digest" | "research";

export interface LibraryIssue {
  id: string;
  collection: LibraryCollection;
  date: string;
  publishedAt: string;
  title: string;
  references: string[];
  canonicalPath: string;
  text: string;
  sourceHtml: string;
  coverPath: string | null;
  version: number;
  updatedAt: string;
}

export type LibraryIssueSummary = Pick<
  LibraryIssue,
  | "id"
  | "collection"
  | "date"
  | "publishedAt"
  | "title"
  | "canonicalPath"
  | "coverPath"
  | "version"
  | "updatedAt"
>;

export type LibraryMutationResult = {
  status: "created" | "updated" | "unchanged";
  issue: LibraryIssue;
};

export interface LibraryAssetResult {
  status: "stored";
  path: string;
  bytes: number;
}

export interface Env {
  DB: D1Database;
  MEDIA: R2Bucket;
  AUTH_SERVICE: AuthServiceBinding;
  AUTH_ISSUER: string;
  RESOURCE_URI: string;
  LIBRARY_SITE_TOKEN: string;
}
