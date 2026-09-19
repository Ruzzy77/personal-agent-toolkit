import { ownerServiceFetch } from '@/lib/owner-service';

export type LibraryCollection = 'daily' | 'digest' | 'research';

export type LibraryIssue = {
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
};

export type LibraryIssueSummary = Pick<
  LibraryIssue,
  | 'id'
  | 'collection'
  | 'date'
  | 'publishedAt'
  | 'title'
  | 'canonicalPath'
  | 'coverPath'
  | 'version'
  | 'updatedAt'
>;

export type LibraryMutationResult = {
  status: 'created' | 'updated' | 'unchanged';
  issue: LibraryIssue;
};

type ServiceEnvelope<T> =
  | { ok: true; result: T }
  | { ok: false; error: { code: string; message: string; details?: Record<string, unknown> } };

class ProductRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function requestResult<T>(
  product: 'library' | 'design',
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await ownerServiceFetch('/' + product + path, init);
  let envelope: ServiceEnvelope<T>;
  try {
    envelope = (await response.json()) as ServiceEnvelope<T>;
  } catch {
    throw new ProductRequestError(
      product + '_invalid_response',
      product + ' response was invalid',
      response.status,
    );
  }
  if (!response.ok || !envelope.ok) {
    if (!envelope.ok) {
      throw new ProductRequestError(
        envelope.error.code,
        envelope.error.message,
        response.status,
        envelope.error.details,
      );
    }
    throw new ProductRequestError(
      product + '_request_failed',
      product + ' request failed (' + response.status + ')',
      response.status,
    );
  }
  return envelope.result;
}

function apiError(product: 'library' | 'design', error: unknown): Response {
  if (error instanceof ProductRequestError) {
    return Response.json(
      { error: error.code, details: error.details },
      { status: error.status },
    );
  }
  return Response.json({ error: product + '_service_error' }, { status: 502 });
}

export function libraryRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  return requestResult<T>('library', path, init);
}

export function libraryFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  return ownerServiceFetch('/library' + path, init);
}

export function libraryApiError(error: unknown): Response {
  return apiError('library', error);
}
