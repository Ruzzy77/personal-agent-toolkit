export type OwnerIdentity = { ownerId?: string; userId?: string; csrf: string };
let cached: Promise<OwnerIdentity> | undefined;
export function ownerIdentity(): Promise<OwnerIdentity> {
  return cached ??= fetch("/api/identity", { cache: "no-store", credentials: "same-origin" })
    .then(async response => {
      if (!response.ok) throw new Error("로그인이 필요합니다.");
      return response.json() as Promise<OwnerIdentity>;
    }).catch(error => { cached = undefined; throw error; });
}
export function clearOwnerIdentity() { cached = undefined; }
export async function ownerFetch(input: string | URL | Request, init: RequestInit = {}): Promise<Response> {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
  if (url.origin !== location.origin) throw new Error("다른 주소로 인증 정보를 보낼 수 없습니다.");
  const method = (init.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init.headers).forEach((value, name) => headers.set(name, value));
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-Toolkit-CSRF", (await ownerIdentity()).csrf);
  return fetch(input, { ...init, headers, credentials: "same-origin", cache: "no-store" });
}
