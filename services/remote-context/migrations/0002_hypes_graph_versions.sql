-- Keep revision metadata separate from the user's relationship graph.
CREATE TABLE hypes_graph_versions (
  owner_id TEXT PRIMARY KEY NOT NULL,
  version TEXT NOT NULL CHECK (length(version) = 32)
);

INSERT INTO hypes_graph_versions (owner_id, version)
SELECT owner_id, lower(hex(randomblob(16))) FROM (
  SELECT owner_id FROM hypes_nodes
  UNION SELECT owner_id FROM hypes_predicates
  UNION SELECT owner_id FROM hypes_edges
);

CREATE TRIGGER hypes_nodes_version_insert
AFTER INSERT ON hypes_nodes BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_nodes_version_update
AFTER UPDATE ON hypes_nodes BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
  INSERT INTO hypes_graph_versions (owner_id, version)
  SELECT old.owner_id, lower(hex(randomblob(16)))
  WHERE old.owner_id <> new.owner_id
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_nodes_version_delete
AFTER DELETE ON hypes_nodes BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (old.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_predicates_version_insert
AFTER INSERT ON hypes_predicates BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_predicates_version_update
AFTER UPDATE ON hypes_predicates BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
  INSERT INTO hypes_graph_versions (owner_id, version)
  SELECT old.owner_id, lower(hex(randomblob(16)))
  WHERE old.owner_id <> new.owner_id
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_predicates_version_delete
AFTER DELETE ON hypes_predicates BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (old.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_edges_version_insert
AFTER INSERT ON hypes_edges BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_edges_version_update
AFTER UPDATE ON hypes_edges BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (new.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
  INSERT INTO hypes_graph_versions (owner_id, version)
  SELECT old.owner_id, lower(hex(randomblob(16)))
  WHERE old.owner_id <> new.owner_id
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

CREATE TRIGGER hypes_edges_version_delete
AFTER DELETE ON hypes_edges BEGIN
  INSERT INTO hypes_graph_versions (owner_id, version)
  VALUES (old.owner_id, lower(hex(randomblob(16))))
  ON CONFLICT(owner_id) DO UPDATE SET version = excluded.version;
END;

