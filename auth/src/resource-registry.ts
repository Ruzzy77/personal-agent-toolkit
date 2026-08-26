export interface ResourceDefinition {
  id: string;
  name: string;
  resource: string;
  scopes: string[];
  baselineScopes: string[];
}

export interface ResourceRegistry {
  resources: ResourceDefinition[];
}

const SCOPE_PATTERN = /^[\x21\x23-\x5B\x5D-\x7E]+$/;

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new TypeError(`${field} must be a non-empty string`);
  }
  return value;
}

function requireScopes(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new TypeError(`${field} must be a non-empty array`);
  }

  const scopes = value.map((item, index) =>
    requireString(item, `${field}[${index}]`),
  );
  if (new Set(scopes).size !== scopes.length) {
    throw new TypeError(`${field} must not contain duplicates`);
  }
  if (scopes.some((scope) => !SCOPE_PATTERN.test(scope))) {
    throw new TypeError(`${field} contains an invalid OAuth scope token`);
  }
  return scopes;
}

function requireCanonicalResource(value: unknown, field: string): string {
  const resource = requireString(value, field);
  const url = new URL(resource);
  if (
    url.protocol !== "https:" ||
    url.username !== "" ||
    url.password !== "" ||
    url.hash !== ""
  ) {
    throw new TypeError(`${field} must be a canonical HTTPS resource URI`);
  }
  return url.href;
}

export function parseResourceRegistry(input: string): ResourceRegistry {
  let raw: unknown;
  try {
    raw = JSON.parse(input);
  } catch {
    throw new TypeError("RESOURCE_REGISTRY_JSON must be valid JSON");
  }

  if (
    typeof raw !== "object" ||
    raw === null ||
    !("resources" in raw) ||
    !Array.isArray(raw.resources) ||
    raw.resources.length === 0
  ) {
    throw new TypeError("resource registry must contain resources");
  }

  const resources = raw.resources.map((item, index): ResourceDefinition => {
    if (typeof item !== "object" || item === null) {
      throw new TypeError(`resources[${index}] must be an object`);
    }

    const record = item as Record<string, unknown>;
    const scopes = requireScopes(record.scopes, `resources[${index}].scopes`);
    const baselineScopes = requireScopes(
      record.baselineScopes,
      `resources[${index}].baselineScopes`,
    );
    if (baselineScopes.some((scope) => !scopes.includes(scope))) {
      throw new TypeError(
        `resources[${index}].baselineScopes must be included in scopes`,
      );
    }

    return {
      id: requireString(record.id, `resources[${index}].id`),
      name: requireString(record.name, `resources[${index}].name`),
      resource: requireCanonicalResource(
        record.resource,
        `resources[${index}].resource`,
      ),
      scopes,
      baselineScopes,
    };
  });

  const ids = resources.map((item) => item.id);
  const resourceUris = resources.map((item) => item.resource);
  if (new Set(ids).size !== ids.length) {
    throw new TypeError("resource ids must be unique");
  }
  if (new Set(resourceUris).size !== resourceUris.length) {
    throw new TypeError("resource URIs must be unique");
  }

  return { resources };
}

export function findResource(
  registry: ResourceRegistry,
  resource: string,
): ResourceDefinition | undefined {
  return registry.resources.find((item) => item.resource === resource);
}
