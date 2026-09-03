import {
  serviceFetch,
  serviceRequest,
  ServiceRequestError,
} from "@personal-agent/site-runtime";

function configuration(): { baseUrl: string; token: string } {
  const baseUrl = process.env.DESIGN_SERVICE_URL;
  const token = process.env.DESIGN_SITE_TOKEN;
  if (!baseUrl || !token) throw new Error("Design service is not configured");
  return { baseUrl, token };
}

export function designRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return serviceRequest<T>({
    ...configuration(),
    path,
    serviceName: "design",
    ...(init ? { init } : {}),
  });
}

export function designFetch(path: string, init?: RequestInit): Promise<Response> {
  return serviceFetch({
    ...configuration(),
    path,
    ...(init ? { init } : {}),
  });
}

export function designApiError(error: unknown): Response {
  if (error instanceof ServiceRequestError) {
    return Response.json(
      { error: error.code, details: error.details },
      { status: error.status },
    );
  }
  return Response.json({ error: "design_service_error" }, { status: 502 });
}
