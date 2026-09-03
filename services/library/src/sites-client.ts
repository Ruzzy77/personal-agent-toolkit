import type {
  Env,
  LibraryCreateInput,
  LibraryIssue,
  LibraryIssueSummary,
  LibraryMutationResult,
  LibraryUpdateInput,
} from "./types";

export class SitesLibraryError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
  }
}

export class SitesLibraryClient {
  constructor(private readonly env: Env) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set(
      "OAI-Sites-Authorization",
      `Bearer ${this.env.SITES_BYPASS_TOKEN}`,
    );
    headers.set("x-library-bridge-secret", this.env.LIBRARY_BRIDGE_SECRET);
    if (init.body && !headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }

    const response = await fetch(new URL(path, this.env.SITES_ORIGIN), {
      ...init,
      headers,
    });
    const body = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) {
      throw new SitesLibraryError(
        response.status,
        typeof body.error === "string" ? body.error : `sites_http_${response.status}`,
      );
    }
    return body as T;
  }

  async listIssues(
    collection: string | null,
    limit: number,
  ): Promise<LibraryIssueSummary[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (collection) query.set("collection", collection);
    const result = await this.request<{ issues: LibraryIssueSummary[] }>(
      `/api/library/issues?${query}`,
    );
    return result.issues;
  }

  async readIssue(id: string): Promise<LibraryIssue | null> {
    try {
      const result = await this.request<{ issue: LibraryIssue }>(
        `/api/library/issues/${encodeURIComponent(id)}`,
      );
      return result.issue;
    } catch (error) {
      if (error instanceof SitesLibraryError && error.status === 404) return null;
      throw error;
    }
  }

  async updateIssue(
    id: string,
    input: LibraryUpdateInput,
  ): Promise<LibraryMutationResult> {
    return this.request<LibraryMutationResult>(
      `/api/library/issues/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
      },
    );
  }

  async createIssue(input: LibraryCreateInput): Promise<LibraryMutationResult> {
    return this.request<LibraryMutationResult>("/api/library/issues", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async uploadAsset(
    path: string,
    contentType: string,
    bytes: Uint8Array,
  ): Promise<{ status: string; path: string; bytes: number }> {
    return this.request(
      `/api/library/assets/${path.split("/").map(encodeURIComponent).join("/")}`,
      {
        method: "PUT",
        headers: { "content-type": contentType },
        body: bytes.slice().buffer as ArrayBuffer,
      },
    );
  }
}
