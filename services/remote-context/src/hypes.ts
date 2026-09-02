import { canonicalJson } from "./canonical";
import { ContextError } from "./errors";
import { hypesReadSchema, hypesRewriteSchema } from "./schemas";

type Kind = "node" | "pred" | "edge";

interface NodeRow {
  node_id: string;
  labels_json: string;
  name: string;
  description: string;
  aliases_json: string;
  attributes_json: string;
}

interface PredicateRow {
  predicate_id: string;
  name: string;
  description: string;
  aliases_json: string;
}

interface EdgeRow {
  edge_id: string;
  source_id: string;
  predicate_id: string;
  target_id: string;
  qualifiers_json: string;
}

type RewriteOperation = ReturnType<typeof hypesRewriteSchema.parse>["operations"][number];

type NormalizedOperation =
  | { op: "put_node"; ref: string; value: Extract<RewriteOperation, { op: "put_node" }>["value"] }
  | {
      op: "put_predicate";
      ref: string;
      value: Extract<RewriteOperation, { op: "put_predicate" }>["value"];
    }
  | {
      op: "put_edge";
      ref: string;
      value: Extract<RewriteOperation, { op: "put_edge" }>["value"];
    }
  | { op: "delete"; ref: string; kind: Kind };

const PERSISTENT_REF = /^(node|pred|edge)_[0-9a-f]{32}$/;
const FOCUS_TOKEN = /[^\W_]+/gu;
const OUTLINE_CONTINUATION = /^outline-v1:([1-9][0-9]{0,9})$/;
const MAX_FOCUS_SEEDS = 12;
const EDGE_EXPANSION_RESERVE = 3;

function newRef(kind: Kind): string {
  return `${kind}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function refKind(ref: string): Kind | null {
  return (PERSISTENT_REF.exec(ref)?.[1] as Kind | undefined) ?? null;
}

function parseStringArray(value: string): string[] {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === "string")) {
    throw new ContextError("invalid_stored_graph", "the stored Hypes graph is invalid", 500);
  }
  return parsed;
}

function parseObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new ContextError("invalid_stored_graph", "the stored Hypes graph is invalid", 500);
  }
  return parsed as Record<string, unknown>;
}

function nodeValue(row: NodeRow) {
  return {
    node_id: row.node_id,
    labels: parseStringArray(row.labels_json),
    name: row.name,
    description: row.description || null,
    aliases: parseStringArray(row.aliases_json),
    attributes: parseObject(row.attributes_json),
  };
}

function predicateValue(row: PredicateRow) {
  return {
    predicate_id: row.predicate_id,
    name: row.name,
    description: row.description || null,
    aliases: parseStringArray(row.aliases_json),
  };
}

function edgeValue(row: EdgeRow) {
  return {
    edge_id: row.edge_id,
    source_id: row.source_id,
    predicate_id: row.predicate_id,
    target_id: row.target_id,
    qualifiers: parseObject(row.qualifiers_json),
  };
}

function ftsQueries(focus: string): string[] {
  const tokens = [...new Set(focus.toLocaleLowerCase().match(FOCUS_TOKEN) ?? [])].slice(0, 16);
  if (tokens.length === 0) return [];
  const terms = tokens.map((token) => `"${token.replaceAll('"', '""')}"*`);
  const exact = terms.join(" AND ");
  return terms.length === 1 ? [exact] : [exact, terms.join(" OR ")];
}

function outlineOffset(continuation?: string | null): number {
  if (continuation == null) return 0;
  const match = OUTLINE_CONTINUATION.exec(continuation);
  if (!match) {
    throw new ContextError(
      "invalid_read",
      "continuation must be an outline cursor returned by hypes_read",
    );
  }
  return Number(match[1]);
}

function resolveRef(
  ref: string,
  expectedKind: Kind,
  refMap: Map<string, string>,
  temporaryKinds: Map<string, Kind>,
): string {
  if (ref.startsWith("$")) {
    const actualKind = temporaryKinds.get(ref);
    if (!actualKind) {
      throw new ContextError(
        "object_not_found",
        "an edge refers to an undefined temporary object",
        404,
        { ref },
      );
    }
    if (actualKind !== expectedKind) {
      throw new ContextError(
        "reference_type_mismatch",
        "an edge reference has the wrong ontology object type",
        400,
        { ref, expected_type: expectedKind },
      );
    }
    return refMap.get(ref)!;
  }
  if (refKind(ref) !== expectedKind) {
    throw new ContextError(
      "reference_type_mismatch",
      "an edge reference has the wrong ontology object type",
      400,
      { ref, expected_type: expectedKind },
    );
  }
  return ref;
}

export class HypesService {
  constructor(
    private readonly db: D1Database,
    private readonly ownerId: string,
  ) {}

  private async allRows(): Promise<{
    nodes: Map<string, NodeRow>;
    predicates: Map<string, PredicateRow>;
    edges: Map<string, EdgeRow>;
  }> {
    const [nodes, predicates, edges] = await Promise.all([
      this.db
        .prepare(
          `SELECT node_id, labels_json, name, description, aliases_json, attributes_json
           FROM hypes_nodes WHERE owner_id = ? ORDER BY node_id`,
        )
        .bind(this.ownerId)
        .all<NodeRow>(),
      this.db
        .prepare(
          `SELECT predicate_id, name, description, aliases_json
           FROM hypes_predicates WHERE owner_id = ? ORDER BY predicate_id`,
        )
        .bind(this.ownerId)
        .all<PredicateRow>(),
      this.db
        .prepare(
          `SELECT edge_id, source_id, predicate_id, target_id, qualifiers_json
           FROM hypes_edges WHERE owner_id = ? ORDER BY edge_id`,
        )
        .bind(this.ownerId)
        .all<EdgeRow>(),
    ]);
    return {
      nodes: new Map(nodes.results.map((row) => [row.node_id, row])),
      predicates: new Map(predicates.results.map((row) => [row.predicate_id, row])),
      edges: new Map(edges.results.map((row) => [row.edge_id, row])),
    };
  }

  async rewrite(input: unknown): Promise<Record<string, unknown>> {
    const operations = hypesRewriteSchema.parse(input).operations;
    const refMap = new Map<string, string>();
    const temporaryKinds = new Map<string, Kind>();
    const targetRefs = new Set<string>();
    const normalized: NormalizedOperation[] = [];

    for (const operation of operations) {
      if (operation.op === "delete") {
        if (targetRefs.has(operation.ref)) {
          throw new ContextError(
            "duplicate_ref",
            "an ontology patch may target each object only once",
            400,
            { ref: operation.ref },
          );
        }
        targetRefs.add(operation.ref);
        normalized.push({ op: "delete", ref: operation.ref, kind: refKind(operation.ref)! });
        continue;
      }

      const kind: Kind =
        operation.op === "put_node"
          ? "node"
          : operation.op === "put_predicate"
            ? "pred"
            : "edge";
      let persistentRef = operation.ref;
      if (operation.ref.startsWith("$")) {
        if (temporaryKinds.has(operation.ref)) {
          throw new ContextError(
            "duplicate_ref",
            "a temporary ref may be defined only once",
            400,
            { ref: operation.ref },
          );
        }
        temporaryKinds.set(operation.ref, kind);
        persistentRef = newRef(kind);
        refMap.set(operation.ref, persistentRef);
      }
      if (targetRefs.has(persistentRef)) {
        throw new ContextError(
          "duplicate_ref",
          "an ontology patch may target each object only once",
          400,
          { ref: persistentRef },
        );
      }
      targetRefs.add(persistentRef);
      if (operation.op === "put_node") {
        normalized.push({ op: operation.op, ref: persistentRef, value: operation.value });
      } else if (operation.op === "put_predicate") {
        normalized.push({ op: operation.op, ref: persistentRef, value: operation.value });
      } else {
        normalized.push({
          op: operation.op,
          ref: persistentRef,
          value: {
            ...operation.value,
            source_ref: resolveRef(
              operation.value.source_ref,
              "node",
              refMap,
              temporaryKinds,
            ),
            predicate_ref: resolveRef(
              operation.value.predicate_ref,
              "pred",
              refMap,
              temporaryKinds,
            ),
            target_ref: resolveRef(
              operation.value.target_ref,
              "node",
              refMap,
              temporaryKinds,
            ),
          },
        });
      }
    }

    const deletedNodes = new Set(
      normalized
        .filter((operation) => operation.op === "delete" && operation.kind === "node")
        .map((operation) => operation.ref),
    );
    const deletedPredicates = new Set(
      normalized
        .filter((operation) => operation.op === "delete" && operation.kind === "pred")
        .map((operation) => operation.ref),
    );
    for (const operation of normalized) {
      if (
        operation.op === "put_edge" &&
        (deletedNodes.has(operation.value.source_ref) ||
          deletedNodes.has(operation.value.target_ref) ||
          deletedPredicates.has(operation.value.predicate_ref))
      ) {
        throw new ContextError(
          "dangling_edge",
          "an edge cannot refer to an entity deleted by the same patch",
        );
      }
    }

    const current = await this.allRows();
    const createdRefs = new Set(refMap.values());
    for (const operation of normalized) {
      if (createdRefs.has(operation.ref)) continue;
      const exists =
        operation.op === "put_node"
          ? current.nodes.has(operation.ref)
          : operation.op === "put_predicate"
            ? current.predicates.has(operation.ref)
            : operation.op === "put_edge"
              ? current.edges.has(operation.ref)
              : operation.kind === "node"
                ? current.nodes.has(operation.ref)
                : operation.kind === "pred"
                  ? current.predicates.has(operation.ref)
                  : current.edges.has(operation.ref);
      if (!exists) {
        throw new ContextError(
          "object_not_found",
          "the ontology object does not exist",
          404,
          { ref: operation.ref },
        );
      }
    }

    const prospectiveNodes = new Set(current.nodes.keys());
    const prospectivePredicates = new Set(current.predicates.keys());
    for (const operation of normalized) {
      if (operation.op === "put_node") prospectiveNodes.add(operation.ref);
      else if (operation.op === "put_predicate") prospectivePredicates.add(operation.ref);
      else if (operation.op === "delete" && operation.kind === "node") {
        prospectiveNodes.delete(operation.ref);
      } else if (operation.op === "delete" && operation.kind === "pred") {
        prospectivePredicates.delete(operation.ref);
      }
    }
    for (const operation of normalized) {
      if (
        operation.op === "put_edge" &&
        (!prospectiveNodes.has(operation.value.source_ref) ||
          !prospectiveNodes.has(operation.value.target_ref) ||
          !prospectivePredicates.has(operation.value.predicate_ref))
      ) {
        throw new ContextError(
          "dangling_edge",
          "every stored edge endpoint must remain valid",
        );
      }
    }

    const statements: D1PreparedStatement[] = [];
    for (const operation of normalized) {
      if (operation.op === "put_node") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_nodes(
                 owner_id, node_id, labels_json, name, description, aliases_json, attributes_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_id, node_id) DO UPDATE SET
                 labels_json = excluded.labels_json,
                 name = excluded.name,
                 description = excluded.description,
                 aliases_json = excluded.aliases_json,
                 attributes_json = excluded.attributes_json`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              canonicalJson(operation.value.labels),
              operation.value.name,
              operation.value.description ?? "",
              canonicalJson(operation.value.aliases),
              canonicalJson(operation.value.attributes),
            ),
        );
      } else if (operation.op === "put_predicate") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_predicates(
                 owner_id, predicate_id, name, description, aliases_json
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(owner_id, predicate_id) DO UPDATE SET
                 name = excluded.name,
                 description = excluded.description,
                 aliases_json = excluded.aliases_json`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              operation.value.name,
              operation.value.description ?? "",
              canonicalJson(operation.value.aliases),
            ),
        );
      }
    }
    for (const operation of normalized) {
      if (operation.op === "delete" && operation.kind === "edge") {
        statements.push(
          this.db
            .prepare("DELETE FROM hypes_edges WHERE owner_id = ? AND edge_id = ?")
            .bind(this.ownerId, operation.ref),
        );
      }
    }
    for (const operation of normalized) {
      if (operation.op === "put_edge") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_edges(
                 owner_id, edge_id, source_id, predicate_id, target_id, qualifiers_json
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_id, edge_id) DO UPDATE SET
                 source_id = excluded.source_id,
                 predicate_id = excluded.predicate_id,
                 target_id = excluded.target_id,
                 qualifiers_json = excluded.qualifiers_json`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              operation.value.source_ref,
              operation.value.predicate_ref,
              operation.value.target_ref,
              canonicalJson(operation.value.qualifiers),
            ),
        );
      }
    }

    const cascadedEdges = new Set<string>();
    for (const operation of normalized) {
      if (operation.op !== "delete" || operation.kind === "edge") continue;
      for (const edge of current.edges.values()) {
        if (
          (operation.kind === "node" &&
            (edge.source_id === operation.ref || edge.target_id === operation.ref)) ||
          (operation.kind === "pred" && edge.predicate_id === operation.ref)
        ) {
          cascadedEdges.add(edge.edge_id);
        }
      }
      const table = operation.kind === "node" ? "hypes_nodes" : "hypes_predicates";
      const column = operation.kind === "node" ? "node_id" : "predicate_id";
      statements.push(
        this.db
          .prepare(`DELETE FROM ${table} WHERE owner_id = ? AND ${column} = ?`)
          .bind(this.ownerId, operation.ref),
      );
    }

    if (statements.length > 0) {
      try {
        await this.db.batch(statements);
      } catch (error) {
        throw new ContextError(
          "dangling_edge",
          "every stored edge endpoint must remain valid",
          409,
          { cause: error instanceof Error ? error.message : String(error) },
        );
      }
    }

    const upsertedRefs = normalized
      .filter((operation) => operation.op !== "delete")
      .map((operation) => operation.ref);
    const removedRefs = normalized
      .filter((operation) => operation.op === "delete")
      .map((operation) => operation.ref);
    for (const ref of [...cascadedEdges].sort()) {
      if (!removedRefs.includes(ref)) removedRefs.push(ref);
    }
    const changeSummary = {
      created: { nodes: 0, predicates: 0, edges: 0 },
      updated: { nodes: 0, predicates: 0, edges: 0 },
      deleted: { nodes: 0, predicates: 0, edges: 0 },
    };
    const label = { node: "nodes", pred: "predicates", edge: "edges" } as const;
    for (const operation of normalized) {
      const kind: Kind =
        operation.op === "delete"
          ? operation.kind
          : operation.op === "put_node"
            ? "node"
            : operation.op === "put_predicate"
              ? "pred"
              : "edge";
      const action =
        operation.op === "delete"
          ? "deleted"
          : createdRefs.has(operation.ref)
            ? "created"
            : "updated";
      changeSummary[action][label[kind]] += 1;
    }
    changeSummary.deleted.edges += cascadedEdges.size;

    return {
      ref_map: Object.fromEntries(refMap),
      upserted_refs: upsertedRefs,
      removed_refs: removedRefs,
      change_summary: changeSummary,
    };
  }

  private async focusSeeds(focus: string, limit: number): Promise<Array<[Kind, string]>> {
    for (const query of ftsQueries(focus)) {
      const [nodes, predicates] = await Promise.all([
        this.db
          .prepare(
            `SELECT ref, bm25(hypes_nodes_fts) AS rank
             FROM hypes_nodes_fts
             WHERE hypes_nodes_fts MATCH ? AND owner_id = ?
             ORDER BY rank, ref LIMIT ?`,
          )
          .bind(query, this.ownerId, limit)
          .all<{ ref: string; rank: number }>(),
        this.db
          .prepare(
            `SELECT ref, bm25(hypes_predicates_fts) AS rank
             FROM hypes_predicates_fts
             WHERE hypes_predicates_fts MATCH ? AND owner_id = ?
             ORDER BY rank, ref LIMIT ?`,
          )
          .bind(query, this.ownerId, limit)
          .all<{ ref: string; rank: number }>(),
      ]);
      const seeds: Array<[number, Kind, string]> = [
        ...nodes.results.map((row): [number, Kind, string] => [row.rank, "node", row.ref]),
        ...predicates.results.map(
          (row): [number, Kind, string] => [row.rank, "pred", row.ref],
        ),
      ];
      if (seeds.length > 0) {
        seeds.sort((left, right) =>
          left[0] - right[0] || left[1].localeCompare(right[1]) || left[2].localeCompare(right[2]),
        );
        return seeds.slice(0, limit).map(([, kind, ref]) => [kind, ref]);
      }
    }
    return [];
  }

  async read(input: unknown): Promise<Record<string, unknown>> {
    const parsed = hypesReadSchema.parse(input);
    const requestedRefs = [...(parsed.seed_refs ?? [])];
    const offset = outlineOffset(parsed.continuation);
    if (parsed.continuation != null && (parsed.focus != null || requestedRefs.length > 0)) {
      throw new ContextError(
        "invalid_read",
        "continuation can be used only for an outline read without focus or seed_refs",
      );
    }

    const rows = await this.allRows();
    const seeds: Array<[Kind, string]> = [];
    const seen = new Set<string>();
    for (const ref of requestedRefs) {
      const kind = refKind(ref);
      const exists = kind === "node" ? rows.nodes.has(ref) : rows.predicates.has(ref);
      if (!exists) {
        throw new ContextError(
          "object_not_found",
          "a requested ontology seed does not exist",
          404,
          { ref },
        );
      }
      if (!seen.has(ref)) {
        seeds.push([kind!, ref]);
        seen.add(ref);
      }
    }

    let continuation: string | null = null;
    if (parsed.focus != null) {
      const remaining = Math.max(0, parsed.limit - seeds.length);
      let capacity = Math.min(MAX_FOCUS_SEEDS, remaining);
      if (parsed.max_hops > 0 && capacity > 0) {
        capacity = Math.min(capacity, Math.max(0, remaining - EDGE_EXPANSION_RESERVE));
        if (seeds.length === 0) capacity = Math.max(1, capacity);
      }
      if (capacity > 0) {
        const candidates = await this.focusSeeds(parsed.focus, capacity + seen.size);
        let added = 0;
        for (const candidate of candidates) {
          if (seen.has(candidate[1])) continue;
          seeds.push(candidate);
          seen.add(candidate[1]);
          added += 1;
          if (added >= capacity) break;
        }
      }
    } else if (seeds.length === 0) {
      const outline: Array<[string, Kind, string]> = [
        ...[...rows.nodes.values()].map(
          (row): [string, Kind, string] => [row.name.toLocaleLowerCase(), "node", row.node_id],
        ),
        ...[...rows.predicates.values()].map(
          (row): [string, Kind, string] => [
            row.name.toLocaleLowerCase(),
            "pred",
            row.predicate_id,
          ],
        ),
      ];
      outline.sort((left, right) =>
        left[0].localeCompare(right[0]) ||
        left[1].localeCompare(right[1]) ||
        left[2].localeCompare(right[2]),
      );
      const page = outline.slice(offset, offset + parsed.limit + 1);
      seeds.push(...page.slice(0, parsed.limit).map(([, kind, ref]) => [kind, ref] as [Kind, string]));
      if (page.length > parsed.limit) continuation = `outline-v1:${offset + seeds.length}`;
    }

    const includedNodes = new Set<string>();
    const includedPredicates = new Set<string>();
    const includedEdges = new Set<string>();
    let frontierNodes = new Set<string>();
    let frontierPredicates = new Set<string>();
    for (const [kind, ref] of seeds) {
      if (includedNodes.size + includedPredicates.size >= parsed.limit) break;
      if (kind === "node") {
        includedNodes.add(ref);
        frontierNodes.add(ref);
      } else {
        includedPredicates.add(ref);
        frontierPredicates.add(ref);
      }
    }

    const maxHops = parsed.focus == null && requestedRefs.length === 0 ? 0 : parsed.max_hops;
    const expandedNodes = new Set<string>();
    for (let hop = 0; hop < maxHops; hop += 1) {
      const candidates = [...rows.edges.values()]
        .filter(
          (edge) =>
            !includedEdges.has(edge.edge_id) &&
            (frontierNodes.has(edge.source_id) ||
              frontierNodes.has(edge.target_id) ||
              frontierPredicates.has(edge.predicate_id)),
        )
        .sort((left, right) => left.edge_id.localeCompare(right.edge_id));
      const nextNodes = new Set<string>();
      for (const edge of candidates) {
        const missingNodes = new Set(
          [edge.source_id, edge.target_id].filter((ref) => !includedNodes.has(ref)),
        );
        const missingPredicates = includedPredicates.has(edge.predicate_id)
          ? new Set<string>()
          : new Set([edge.predicate_id]);
        const cost = 1 + missingNodes.size + missingPredicates.size;
        if (includedNodes.size + includedPredicates.size + includedEdges.size + cost > parsed.limit) {
          continue;
        }
        includedEdges.add(edge.edge_id);
        for (const ref of missingNodes) includedNodes.add(ref);
        for (const ref of missingPredicates) includedPredicates.add(ref);
        nextNodes.add(edge.source_id);
        nextNodes.add(edge.target_id);
      }
      for (const ref of frontierNodes) expandedNodes.add(ref);
      frontierNodes = new Set([...nextNodes].filter((ref) => !expandedNodes.has(ref)));
      frontierPredicates = new Set();
      if (frontierNodes.size === 0) break;
    }

    return {
      nodes: [...includedNodes].sort().map((ref) => nodeValue(rows.nodes.get(ref)!)),
      predicates: [...includedPredicates]
        .sort()
        .map((ref) => predicateValue(rows.predicates.get(ref)!)),
      edges: [...includedEdges].sort().map((ref) => edgeValue(rows.edges.get(ref)!)),
      continuation,
    };
  }

  async importGraph(value: unknown): Promise<Record<string, unknown>> {
    const graph = value as {
      nodes?: unknown[];
      predicates?: unknown[];
      edges?: unknown[];
    };
    if (!graph || typeof graph !== "object") {
      throw new ContextError("invalid_import", "the Hypes import is invalid");
    }
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const predicates = Array.isArray(graph.predicates) ? graph.predicates : [];
    const edges = Array.isArray(graph.edges) ? graph.edges : [];
    const operations: unknown[] = [
      ...nodes.map((node) => ({
        op: "put_node",
        ref: (node as { node_id: string }).node_id,
        value: {
          labels: (node as { labels?: unknown }).labels ?? [],
          name: (node as { name: string }).name,
          description: (node as { description?: unknown }).description ?? null,
          aliases: (node as { aliases?: unknown }).aliases ?? [],
          attributes: (node as { attributes?: unknown }).attributes ?? {},
        },
      })),
      ...predicates.map((predicate) => ({
        op: "put_predicate",
        ref: (predicate as { predicate_id: string }).predicate_id,
        value: {
          name: (predicate as { name: string }).name,
          description: (predicate as { description?: unknown }).description ?? null,
          aliases: (predicate as { aliases?: unknown }).aliases ?? [],
        },
      })),
      ...edges.map((edge) => ({
        op: "put_edge",
        ref: (edge as { edge_id: string }).edge_id,
        value: {
          source_ref: (edge as { source_id: string }).source_id,
          predicate_ref: (edge as { predicate_id: string }).predicate_id,
          target_ref: (edge as { target_id: string }).target_id,
          qualifiers: (edge as { qualifiers?: unknown }).qualifiers ?? {},
        },
      })),
    ];

    hypesRewriteSchema.parse({ operations });
    const existing = await this.allRows();
    const statements: D1PreparedStatement[] = [
      this.db.prepare("DELETE FROM hypes_edges WHERE owner_id = ?").bind(this.ownerId),
      this.db.prepare("DELETE FROM hypes_nodes WHERE owner_id = ?").bind(this.ownerId),
      this.db.prepare("DELETE FROM hypes_predicates WHERE owner_id = ?").bind(this.ownerId),
    ];
    void existing;
    for (const operation of operations as Array<Record<string, any>>) {
      if (operation.op === "put_node") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_nodes(
                 owner_id, node_id, labels_json, name, description, aliases_json, attributes_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              canonicalJson(operation.value.labels),
              operation.value.name,
              operation.value.description ?? "",
              canonicalJson(operation.value.aliases),
              canonicalJson(operation.value.attributes),
            ),
        );
      } else if (operation.op === "put_predicate") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_predicates(
                 owner_id, predicate_id, name, description, aliases_json
               ) VALUES (?, ?, ?, ?, ?)`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              operation.value.name,
              operation.value.description ?? "",
              canonicalJson(operation.value.aliases),
            ),
        );
      }
    }
    for (const operation of operations as Array<Record<string, any>>) {
      if (operation.op === "put_edge") {
        statements.push(
          this.db
            .prepare(
              `INSERT INTO hypes_edges(
                 owner_id, edge_id, source_id, predicate_id, target_id, qualifiers_json
               ) VALUES (?, ?, ?, ?, ?, ?)`,
            )
            .bind(
              this.ownerId,
              operation.ref,
              operation.value.source_ref,
              operation.value.predicate_ref,
              operation.value.target_ref,
              canonicalJson(operation.value.qualifiers),
            ),
        );
      }
    }
    await this.db.batch(statements);
    return { nodes: nodes.length, predicates: predicates.length, edges: edges.length };
  }
}
