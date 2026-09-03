import {
  serviceFetch,
  serviceRequest,
  ServiceRequestError,
} from '@personal-agent/site-runtime';

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

function configuration(): { baseUrl: string; token: string } {
  const baseUrl = process.env.LIBRARY_SERVICE_URL;
  const token = process.env.LIBRARY_SITE_TOKEN;
  if (!baseUrl || !token) throw new Error('Library service is not configured');
  return { baseUrl, token };
}

export function libraryRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  return serviceRequest<T>({
    ...configuration(),
    path,
    serviceName: 'library',
    ...(init ? { init } : {}),
  });
}

export function libraryFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  return serviceFetch({
    ...configuration(),
    path,
    ...(init ? { init } : {}),
  });
}

export function libraryApiError(error: unknown): Response {
  if (error instanceof ServiceRequestError) {
    return Response.json(
      { error: error.code, details: error.details },
      { status: error.status },
    );
  }
  return Response.json({ error: 'library_service_error' }, { status: 502 });
}
