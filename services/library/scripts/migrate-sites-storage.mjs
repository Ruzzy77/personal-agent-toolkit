import { createHash } from "node:crypto";

const SOURCE_ISSUES = "/api/library/issues";
const DESTINATION_ISSUES = "/api/v1/issues";
const IMPORT_ISSUES = "/api/v1/import/issues";
const verifyOnly = process.argv.includes("--verify-only");

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const sourceOrigin = required("LIBRARY_SOURCE_URL").replace(/\/$/, "");
const sourceToken = required("LIBRARY_SOURCE_TOKEN");
const destinationOrigin = required("LIBRARY_DESTINATION_URL").replace(/\/$/, "");
const destinationToken = required("LIBRARY_DESTINATION_TOKEN");

async function responseJson(response, label) {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(`${label} failed (${response.status}): ${JSON.stringify(body)}`);
  }
  return body;
}

async function sourceJson(path) {
  return responseJson(
    await fetch(`${sourceOrigin}${path}`, {
      headers: {
        "OAI-Sites-Authorization": `Bearer ${sourceToken}`,
      },
    }),
    `source ${path}`,
  );
}

async function destinationJson(path, init = {}) {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${destinationToken}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const body = await responseJson(
    await fetch(`${destinationOrigin}${path}`, { ...init, headers }),
    `destination ${path}`,
  );
  if (!body?.ok) throw new Error(`destination ${path} returned an invalid envelope`);
  return body.result;
}

async function mapConcurrent(values, concurrency, operation) {
  const results = new Array(values.length);
  let next = 0;
  async function worker() {
    while (next < values.length) {
      const index = next;
      next += 1;
      results[index] = await operation(values[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, values.length) }, worker),
  );
  return results;
}

function mediaPaths(issues) {
  const paths = new Set();
  for (const issue of issues) {
    if (issue.coverPath) paths.add(issue.coverPath);
    for (const match of issue.sourceHtml.matchAll(/["'](\/media\/[^"']+)["']/g)) {
      paths.add(match[1]);
    }
  }
  return [...paths].sort();
}

function issueDigest(issue) {
  return createHash("sha256")
    .update(JSON.stringify([
      issue.id,
      issue.collection,
      issue.date,
      issue.publishedAt,
      issue.title,
      issue.references,
      issue.canonicalPath,
      issue.text,
      issue.sourceHtml,
      issue.coverPath,
      issue.updatedAt,
    ]))
    .digest("hex");
}

function byteDigest(bytes) {
  const data = bytes instanceof ArrayBuffer ? new Uint8Array(bytes) : bytes;
  return createHash("sha256").update(data).digest("hex");
}

async function readSourceIssues() {
  const summaries = (await sourceJson(`${SOURCE_ISSUES}?limit=200`)).issues;
  if (!Array.isArray(summaries) || summaries.length > 200) {
    throw new Error("source issue index is invalid");
  }
  return mapConcurrent(summaries, 8, async ({ id }) => {
    const body = await sourceJson(
      `${SOURCE_ISSUES}/${encodeURIComponent(id)}`,
    );
    if (!body?.issue) throw new Error(`source issue ${id} is missing`);
    return body.issue;
  });
}

function importBody(issue) {
  return {
    id: issue.id,
    collection: issue.collection,
    date: issue.date,
    published_at: issue.publishedAt,
    title: issue.title,
    references: issue.references,
    canonical_path: issue.canonicalPath,
    text: issue.text,
    source_html: issue.sourceHtml,
    cover_path: issue.coverPath,
    updated_at: issue.updatedAt,
  };
}

async function copyIssues(issues) {
  if (verifyOnly) return;
  await mapConcurrent(issues, 6, (issue) =>
    destinationJson(IMPORT_ISSUES, {
      method: "POST",
      body: JSON.stringify(importBody(issue)),
    }));
}

async function copyAssets(paths) {
  if (verifyOnly) return;
  await mapConcurrent(paths, 6, async (path) => {
    if (!path.startsWith("/media/")) {
      throw new Error(`invalid source media path: ${path}`);
    }
    const sourceResponse = await fetch(`${sourceOrigin}${path}`, {
      headers: {
        "OAI-Sites-Authorization": `Bearer ${sourceToken}`,
      },
    });
    if (!sourceResponse.ok) {
      throw new Error(`source media ${path} failed (${sourceResponse.status})`);
    }
    const contentType = sourceResponse.headers.get("Content-Type");
    if (!contentType) throw new Error(`source media ${path} has no content type`);
    const bytes = new Uint8Array(await sourceResponse.arrayBuffer());
    const key = path
      .slice("/media/".length)
      .split("/")
      .map(encodeURIComponent)
      .join("/");
    await destinationJson(`/api/v1/assets/${key}`, {
      method: "PUT",
      headers: { "Content-Type": contentType },
      body: bytes,
    });
  });
}

async function verifyIssues(issues) {
  await mapConcurrent(issues, 8, async (sourceIssue) => {
    const destinationIssue = await destinationJson(
      `${DESTINATION_ISSUES}/${encodeURIComponent(sourceIssue.id)}`,
    );
    if (issueDigest(sourceIssue) !== issueDigest(destinationIssue)) {
      throw new Error(`issue verification failed: ${sourceIssue.id}`);
    }
  });
}

async function verifyAssets(paths) {
  await mapConcurrent(paths, 6, async (path) => {
    const sourceResponse = await fetch(`${sourceOrigin}${path}`, {
      headers: {
        "OAI-Sites-Authorization": `Bearer ${sourceToken}`,
      },
    });
    const destinationResponse = await fetch(`${destinationOrigin}${path}`, {
      headers: { "Authorization": `Bearer ${destinationToken}` },
    });
    if (!sourceResponse.ok || !destinationResponse.ok) {
      throw new Error(`media verification failed: ${path}`);
    }
    const [sourceBytes, destinationBytes] = await Promise.all([
      sourceResponse.arrayBuffer(),
      destinationResponse.arrayBuffer(),
    ]);
    if (byteDigest(sourceBytes) !== byteDigest(destinationBytes)) {
      throw new Error(`media digest differs: ${path}`);
    }
  });
}

const issues = await readSourceIssues();
const assets = mediaPaths(issues);
await copyIssues(issues);
await copyAssets(assets);
await verifyIssues(issues);
await verifyAssets(assets);
console.log(JSON.stringify({
  status: verifyOnly ? "verified" : "migrated",
  issues: issues.length,
  assets: assets.length,
}));
