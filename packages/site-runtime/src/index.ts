export type ChatGPTUser = {
  userId: string;
  displayName: string;
  email: string;
  fullName: string | null;
};

type HeaderReader = Pick<Headers, "get">;

export function chatGPTUserFromHeaders(
  requestHeaders: HeaderReader,
): ChatGPTUser | null {
  const userId = requestHeaders.get("oai-authenticated-user-id");
  const email = requestHeaders.get("oai-authenticated-user-email");
  if (!userId || !email) return null;

  const encodedFullName = requestHeaders.get(
    "oai-authenticated-user-full-name",
  );
  const fullName =
    encodedFullName
    && requestHeaders.get("oai-authenticated-user-full-name-encoding")
      === "percent-encoded-utf-8"
      ? safeDecodeURIComponent(encodedFullName)
      : null;

  return {
    userId,
    displayName: fullName ?? email,
    email,
    fullName,
  };
}

function safeDecodeURIComponent(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

type ServiceEnvelope<T> =
  | { ok: true; result: T }
  | {
      ok: false;
      error: {
        code: string;
        message: string;
        details?: Record<string, unknown>;
      };
    };

export class ServiceRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    code: string,
    message: string,
    status: number,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ServiceRequestError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export async function serviceFetch(options: {
  baseUrl: string;
  token: string;
  path: string;
  init?: RequestInit;
}): Promise<Response> {
  const headers = new Headers(options.init?.headers);
  headers.set("Authorization", `Bearer ${options.token}`);
  if (options.init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(
    `${options.baseUrl.replace(/\/$/, "")}${options.path}`,
    {
      ...options.init,
      cache: "no-store",
      headers,
    },
  );
}

export async function serviceRequest<T>(options: {
  baseUrl: string;
  token: string;
  path: string;
  serviceName: string;
  init?: RequestInit;
}): Promise<T> {
  const response = await serviceFetch(options);
  const envelope = await response.json() as ServiceEnvelope<T>;
  if (!response.ok || !envelope.ok) {
    if (!envelope.ok) {
      throw new ServiceRequestError(
        envelope.error.code,
        envelope.error.message,
        response.status,
        envelope.error.details,
      );
    }
    throw new ServiceRequestError(
      `${options.serviceName}_request_failed`,
      `${options.serviceName} request failed (${response.status})`,
      response.status,
    );
  }
  return envelope.result;
}
