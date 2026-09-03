import type { AuthServiceBinding } from "@personal-agent/remote-runtime";

export type {
  AccessValidationResult,
  AuthenticatedOwner,
  AuthServiceBinding,
} from "@personal-agent/remote-runtime";

export type DesignStatus = "draft" | "candidate" | "validated" | "deprecated";

export type JsonObject = Record<string, unknown>;

export interface DesignFileSummary {
  path: string;
  contentType: string;
  byteSize: number;
  sha256: string;
  revision: number;
  updatedAt: string;
}

export interface DesignRecipeSummary {
  id: string;
  name: string;
  description: string;
  recipeVersion: string;
  status: DesignStatus;
  selectionReady: boolean;
  revision: number;
  updatedAt: string;
}

export interface DesignRecipe extends DesignRecipeSummary {
  metadata: JsonObject;
  files: DesignFileSummary[];
  createdAt: string;
}

export interface DesignCatalog {
  catalog_schema_version: 2;
  library: JsonObject;
  patterns: JsonObject[];
  recipes: JsonObject[];
}

export interface DesignMutationResult {
  status: "created" | "updated" | "unchanged";
  recipe: DesignRecipe;
}

export interface DesignFileResult {
  status: "stored";
  recipeId: string;
  file: DesignFileSummary;
}

export interface Env {
  DB: D1Database;
  ASSETS: R2Bucket;
  AUTH_SERVICE: AuthServiceBinding;
  AUTH_ISSUER: string;
  RESOURCE_URI: string;
  DESIGN_SITE_TOKEN: string;
}
