import assert from "node:assert/strict";
import test from "node:test";

import {
  findLibraryIssues,
  registerCatalogWebMcpTools,
} from "../src/webmcp.js";

const issues = [
  {
    id: "digest:2026-08-25",
    title: "두 개의 경계",
    date: "2026-08-25",
    collection: "digest",
    readerHref: "/editions/digest/issues/2026-08-25",
  },
  {
    id: "research:2026-08-24",
    title: "연속 전파의 조건",
    date: "2026-08-24",
    collection: "research",
    readerHref: "/editions/research/brief/issues/2026-08-24",
  },
];

test("finds Library issues by title, date, id, and collection", () => {
  assert.deepEqual(
    findLibraryIssues(issues, { query: "경계" }).map((issue) => issue.id),
    ["digest:2026-08-25"],
  );
  assert.deepEqual(
    findLibraryIssues(issues, { query: "2026-08-24" }).map((issue) => issue.id),
    ["research:2026-08-24"],
  );
  assert.deepEqual(
    findLibraryIssues(issues, { collection: "research" }).map((issue) => issue.id),
    ["research:2026-08-24"],
  );
});

test("registers page-scoped catalog tools and unregisters them", async () => {
  const registered = [];
  const targetDocument = {
    modelContext: {
      registerTool(tool, options) {
        registered.push({ tool, signal: options.signal });
        return Promise.resolve();
      },
    },
    querySelectorAll() {
      return [];
    },
  };
  let opened = null;
  const targetWindow = {
    location: {
      assign(path) {
        opened = path;
      },
    },
    setTimeout(callback) {
      callback();
      return 1;
    },
  };

  const unregister = registerCatalogWebMcpTools({
    getItems: () => issues,
    targetDocument,
    targetWindow,
  });

  assert.deepEqual(
    registered.map(({ tool }) => tool.name),
    ["library_find_issues", "library_open_issue"],
  );
  assert.equal(registered[0].tool.annotations.readOnlyHint, true);
  assert.equal(registered[1].tool.inputSchema.required[0], "id");

  const found = await registered[0].tool.execute({ query: "연속" });
  assert.equal(found.count, 1);
  assert.equal(found.issues[0].id, "research:2026-08-24");

  const opening = await registered[1].tool.execute({ id: "digest:2026-08-25" });
  assert.equal(opening.status, "opening");
  assert.equal(opened, "/editions/digest/issues/2026-08-25");

  unregister();
  assert.ok(registered.every(({ signal }) => signal.aborted));
});

test("does nothing when WebMCP is unavailable", () => {
  const unregister = registerCatalogWebMcpTools({
    getItems: () => issues,
    targetDocument: { querySelectorAll: () => [] },
    targetWindow: {},
  });
  assert.doesNotThrow(unregister);
});
