import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleScript = path.join(siteRoot, "designs", "tools", "bundle.py");
const committedExport = path.resolve(
  siteRoot,
  "..",
  "..",
  "plugins",
  "design",
  "skills",
  "design",
  "assets",
  "design-library",
);

async function listFiles(root, current = root) {
  const entries = await readdir(current, { withFileTypes: true });
  const files = [];

  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const absolutePath = path.join(current, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listFiles(root, absolutePath));
    } else if (entry.isFile()) {
      files.push(path.relative(root, absolutePath));
    } else {
      throw new Error(`지원하지 않는 묶음 항목: ${absolutePath}`);
    }
  }

  return files;
}

const temporaryRoot = await mkdtemp(path.join(tmpdir(), "design-plugin-export-"));
const freshExport = path.join(temporaryRoot, "design-library");

try {
  await execFileAsync("python3", [bundleScript, "--export-public", freshExport], {
    cwd: siteRoot,
  });

  const [expectedFiles, actualFiles] = await Promise.all([
    listFiles(freshExport),
    listFiles(committedExport),
  ]);
  assert.deepEqual(
    actualFiles,
    expectedFiles,
    "플러그인의 디자인 묶음 파일 목록이 사이트 정본과 다릅니다. npm run export:plugin을 실행해 주세요.",
  );

  const changed = [];
  for (const relativePath of expectedFiles) {
    const [expected, actual] = await Promise.all([
      readFile(path.join(freshExport, relativePath)),
      readFile(path.join(committedExport, relativePath)),
    ]);
    if (!expected.equals(actual)) changed.push(relativePath);
  }

  assert.deepEqual(
    changed,
    [],
    `플러그인의 디자인 묶음이 사이트 정본과 다릅니다: ${changed.join(", ")}`,
  );
  console.log(`플러그인 디자인 묶음 ${expectedFiles.length}개 파일이 사이트 정본과 일치합니다.`);
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
