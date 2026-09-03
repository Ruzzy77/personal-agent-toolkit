import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { basename, extname, join, relative, resolve, sep } from "node:path";

const MIME_TYPES = new Map([
  [".css", "text/css"],
  [".gif", "image/gif"],
  [".html", "text/html"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".json", "application/json"],
  [".md", "text/markdown"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

async function filesUnder(root) {
  const files = [];
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (entry.name === ".DS_Store") continue;
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile()) files.push(path);
    }
  }
  await visit(root);
  return files.sort();
}

function privateMetadata(value) {
  const recipe = structuredClone(value);
  recipe.visibility = "private";
  if (recipe.provenance && typeof recipe.provenance === "object") {
    recipe.provenance.license = "Private";
  }
  return recipe;
}

function privateLibrary(value) {
  const library = structuredClone(value);
  library.license = "Private";
  return library;
}

async function main() {
  const source = resolve(argument("--source") ?? "");
  const serviceUrl = argument("--service-url") ?? process.env.DESIGN_SERVICE_URL;
  const token = process.env.DESIGN_SITE_TOKEN;
  if (!argument("--source") || !serviceUrl || !token) {
    throw new Error(
      "usage: DESIGN_SITE_TOKEN=... node scripts/import-private-library.mjs --source <directory> --service-url <url>",
    );
  }

  const [rawLibrary, rawPatterns] = await Promise.all([
    readFile(join(source, "library.json"), "utf8"),
    readFile(join(source, "patterns.json"), "utf8"),
  ]);
  const library = privateLibrary(JSON.parse(rawLibrary));
  const patternsPayload = JSON.parse(rawPatterns);
  const patterns = patternsPayload.patterns;
  if (!Array.isArray(patterns) || patterns.length === 0) {
    throw new Error("patterns.json does not contain patterns");
  }

  const sharedCorePath = join(source, "shared", "core.css");
  const sharedCore = await readFile(sharedCorePath);
  const entries = await readdir(source, { withFileTypes: true });
  const recipeDirectories = entries
    .filter((entry) => entry.isDirectory() && !["shared", "tools"].includes(entry.name))
    .map((entry) => join(source, entry.name))
    .sort();

  for (const directory of recipeDirectories) {
    const manifestPath = join(directory, "design.json");
    const recipe = privateMetadata(JSON.parse(await readFile(manifestPath, "utf8")));
    if (recipe.id !== basename(directory)) {
      throw new Error(`${relative(source, manifestPath)} has a mismatched id`);
    }
    const sourceFiles = await filesUnder(directory);
    const payloadFiles = [];
    for (const path of sourceFiles) {
      const relativePath = relative(directory, path).split(sep).join("/");
      const bytes = relativePath === "design.json"
        ? Buffer.from(`${JSON.stringify(recipe, null, 2)}\n`)
        : await readFile(path);
      payloadFiles.push({
        path: relativePath,
        content_type: MIME_TYPES.get(extname(path).toLowerCase()) ?? "application/octet-stream",
        base64: bytes.toString("base64"),
        sha256: createHash("sha256").update(bytes).digest("hex"),
      });
    }
    payloadFiles.push({
      path: "shared/core.css",
      content_type: "text/css",
      base64: sharedCore.toString("base64"),
      sha256: createHash("sha256").update(sharedCore).digest("hex"),
    });

    const response = await fetch(
      `${serviceUrl.replace(/\/$/, "")}/api/v1/import/recipes`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ library, patterns, recipe, files: payloadFiles }),
      },
    );
    const result = await response.json();
    if (!response.ok || result.ok !== true) {
      throw new Error(`${recipe.id}: ${response.status} ${JSON.stringify(result)}`);
    }
    console.log(`${recipe.id}: ${result.result.status}`);
  }
}

await main();
