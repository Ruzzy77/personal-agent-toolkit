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
  | "updatedAt"
>;

export type LibraryMutationResult = {
  status: "created" | "updated" | "unchanged";
  issue: LibraryIssue;
};

export interface LibraryCreateInput {
  id: string;
  published_at?: string;
  references?: string[];
  source_html: string;
  cover_path?: string;
}

export interface LibraryUpdateInput {
  source_html: string;
  cover_path?: string;
  references?: string[];
}

export interface AuthenticatedOwner {
  userId: string;
  provider: "google";
  subject: string;
  email?: string;
  displayName?: string;
  resource: string;
  scopes: string[];
  clientId: string;
  expiresAt: number;
}

export type AccessValidationResult =
  | { ok: true; owner: AuthenticatedOwner }
  | {
      ok: false;
      code:
        | "invalid_token"
        | "invalid_target"
        | "invalid_scope"
        | "insufficient_scope";
      status: 401 | 403 | 500;
      requiredScopes?: string[];
    };

export interface AuthServiceBinding {
  validateAccessToken(
    token: string,
    resource: string,
    requiredScopes: string[],
  ): Promise<AccessValidationResult>;
}

export interface Env {
  AUTH_SERVICE: AuthServiceBinding;
  AUTH_ISSUER: string;
  RESOURCE_URI: string;
  SITES_ORIGIN: string;
  SITES_BYPASS_TOKEN: string;
  LIBRARY_BRIDGE_SECRET: string;
}
