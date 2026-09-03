import {
  createIssue,
  isCollection,
  listIssues,
  readIssue,
  readIssueByPath,
  updateIssueFragments,
  updateIssueSource,
} from "./library-repository.js";

const API_ISSUES = "/api/library/issues";
const API_ASSETS = "/api/library/assets/";
const MEDIA = "/media/";
const MAX_ASSET_BYTES = 10 * 1024 * 1024;
const ALLOWED_ASSET_TYPES = new Set([
  "image/avif",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

function json(value, init = {}) {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "private, no-store");
  return new Response(`${JSON.stringify(value)}\n`, { ...init, headers });
}

function errorResponse(error, status = 400) {
  return json({ error: error instanceof Error ? error.message : String(error) }, { status });
}

function hasOwnerIdentity(request) {
  return Boolean(
    request.headers.get("oai-authenticated-user-id")
    || request.headers.get("oai-authenticated-user-email"),
  );
}

function constantTimeEqual(left, right) {
  if (!left || !right || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function hasBridgeAccess(request, env) {
  return constantTimeEqual(
    request.headers.get("x-library-bridge-secret"),
    env.LIBRARY_BRIDGE_SECRET,
  );
}

function canWrite(request, env) {
  return hasOwnerIdentity(request) || hasBridgeAccess(request, env);
}

function issueIdFromApiPath(pathname) {
  if (!pathname.startsWith(`${API_ISSUES}/`)) return null;
  try {
    return decodeURIComponent(pathname.slice(API_ISSUES.length + 1));
  } catch {
    return null;
  }
}

function addConnectSource(policy) {
  if (/\bconnect-src\b/i.test(policy)) return policy;
  return `${policy.trim().replace(/;?$/, ";")} connect-src 'self'`;
}

function addFontSource(policy) {
  if (/\bfont-src\b/i.test(policy)) {
    return policy.replace(/\bfont-src\s+([^;]*)/i, (_, sources) => {
      const values = sources
        .trim()
        .split(/\s+/)
        .filter(Boolean)
        .filter((value) => value !== "'none'");
      if (!values.includes("'self'")) values.push("'self'");
      return `font-src ${values.join(" ")}`;
    });
  }
  return `${policy.trim().replace(/;?$/, ";")} font-src 'self'`;
}

function escapeAttribute(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function addIssueIdentity(html, issue) {
  return html.replace(/<html\b([^>]*)>/i, (_tag, attributes) => {
    const cleanAttributes = attributes.replace(
      /\sdata-library-(?:issue-id|collection|date)=(['"])[\s\S]*?\1/gi,
      "",
    );
    return `<html${cleanAttributes}`
    + ` data-library-issue-id="${escapeAttribute(issue.id)}"`
    + ` data-library-collection="${escapeAttribute(issue.collection)}"`
    + ` data-library-date="${escapeAttribute(issue.date)}">`;
  });
}

function makeIssueEditable(issue, editable) {
  let html = addIssueIdentity(issue.sourceHtml, issue).replace(
    /<meta\b[^>]*http-equiv=["']Content-Security-Policy["'][^>]*>/i,
    (tag) => tag.replace(
      /content=(["'])([\s\S]*?)\1/i,
      (_, quote, policy) => {
        const nextPolicy = editable
          ? addConnectSource(addFontSource(policy))
          : addFontSource(policy);
        return `content=${quote}${nextPolicy}${quote}`;
      },
    ),
  );
  if (!editable) return html;
  const editorAssets = `\n  <link rel="stylesheet" href="/library-editor.css">\n  <script src="/library-editor.js" data-library-issue-id="${issue.id}" defer></script>`;
  html = html.replace(/<\/head>/i, `${editorAssets}\n</head>`);
  return html;
}

function htmlResponse(value) {
  return new Response(value, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "private, no-store",
      "x-robots-tag": "noindex, nofollow",
    },
  });
}

async function handleIssuesApi(request, env, url) {
  if (url.pathname === API_ISSUES && request.method === "GET") {
    const collection = url.searchParams.get("collection");
    if (collection && !isCollection(collection)) return errorResponse("invalid_collection");
    const issues = await listIssues(env.DB, {
      collection,
      limit: url.searchParams.get("limit") ?? 100,
    });
    return json({ issues });
  }

  if (url.pathname === API_ISSUES && request.method === "POST") {
    if (!canWrite(request, env)) return errorResponse("forbidden", 403);
    const input = await request.json();
    const result = await createIssue(env.DB, {
      id: input.id,
      collection: input.collection,
      date: input.date,
      publishedAt: input.published_at ?? input.publishedAt,
      title: input.title,
      references: input.references,
      canonicalPath: input.canonical_path ?? input.canonicalPath,
      sourceHtml: input.source_html ?? input.sourceHtml,
      coverPath: input.cover_path ?? input.coverPath,
    });
    return json(result, { status: result.status === "created" ? 201 : 409 });
  }

  const id = issueIdFromApiPath(url.pathname);
  if (!id) return null;

  if (request.method === "GET") {
    const issue = await readIssue(env.DB, id);
    return issue ? json({ issue }) : errorResponse("not_found", 404);
  }

  if (!canWrite(request, env)) return errorResponse("forbidden", 403);

  if (request.method === "PUT") {
    const input = await request.json();
    const result = await updateIssueSource(
      env.DB,
      id,
      input.source_html ?? input.sourceHtml,
      input.cover_path ?? input.coverPath,
      input.references,
    );
    return result.status === "not_found" ? errorResponse("not_found", 404) : json(result);
  }

  if (request.method === "PATCH") {
    const input = await request.json();
    const result = await updateIssueFragments(env.DB, id, {
      title: input.title,
      leadText: input.lead_text ?? input.leadText,
      articleHtml: input.article_html ?? input.articleHtml,
    });
    return result.status === "not_found" ? errorResponse("not_found", 404) : json(result);
  }

  return errorResponse("method_not_allowed", 405);
}

function assetKeyFromPath(pathname, prefix) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname.slice(prefix.length));
  } catch {
    return null;
  }
  if (!decoded || decoded.startsWith("/") || decoded.includes("..") || !/^[a-zA-Z0-9/_-]+\.[a-zA-Z0-9]+$/.test(decoded)) {
    return null;
  }
  return decoded;
}

async function handleAssetWrite(request, env, url) {
  if (!url.pathname.startsWith(API_ASSETS)) return null;
  if (request.method !== "PUT") return errorResponse("method_not_allowed", 405);
  if (!canWrite(request, env)) return errorResponse("forbidden", 403);
  if (!env.MEDIA) return errorResponse("media_storage_unavailable", 503);
  const key = assetKeyFromPath(url.pathname, API_ASSETS);
  if (!key) return errorResponse("invalid_asset_path");
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (!ALLOWED_ASSET_TYPES.has(contentType)) return errorResponse("invalid_asset_type");
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength < 1 || bytes.byteLength > MAX_ASSET_BYTES) return errorResponse("invalid_asset_size");
  await env.MEDIA.put(key, bytes, { httpMetadata: { contentType } });
  return json({ status: "stored", path: `${MEDIA}${key}`, bytes: bytes.byteLength });
}

async function handleMediaRead(request, env, url) {
  if (!url.pathname.startsWith(MEDIA) || !["GET", "HEAD"].includes(request.method)) return null;
  if (!env.MEDIA) return new Response("Not found", { status: 404 });
  const key = assetKeyFromPath(url.pathname, MEDIA);
  if (!key) return new Response("Not found", { status: 404 });
  const object = await env.MEDIA.get(key);
  if (!object) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  object.writeHttpMetadata?.(headers);
  headers.set("etag", object.httpEtag ?? object.etag);
  headers.set("cache-control", "private, max-age=3600");
  return new Response(request.method === "HEAD" ? null : object.body, { headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    try {
      const issuesApi = await handleIssuesApi(request, env, url);
      if (issuesApi) return issuesApi;

      const assetWrite = await handleAssetWrite(request, env, url);
      if (assetWrite) return assetWrite;

      const media = await handleMediaRead(request, env, url);
      if (media) return media;

      if (["GET", "HEAD"].includes(request.method) && url.pathname.startsWith("/editions/")) {
        const issue = await readIssueByPath(env.DB, url.pathname);
        if (issue) {
          const html = makeIssueEditable(issue, hasOwnerIdentity(request));
          if (request.method === "HEAD") {
            const response = htmlResponse(html);
            return new Response(null, { status: response.status, headers: response.headers });
          }
          return htmlResponse(html);
        }
      }
    } catch (error) {
      return errorResponse(error, 400);
    }

    const response = await env.ASSETS.fetch(request);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");
    if (response.status !== 404 || !acceptsHtml || !["GET", "HEAD"].includes(request.method)) {
      return response;
    }

    if (url.pathname.startsWith("/editions/")) return response;

    const indexUrl = new URL(request.url);
    indexUrl.pathname = "/index.html";
    indexUrl.search = "";
    return env.ASSETS.fetch(new Request(indexUrl, request));
  },
};
