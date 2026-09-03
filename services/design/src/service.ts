import { DesignError } from "./errors";
import {
  createRecipe,
  importRecipe,
  listRecipes,
  readCatalog,
  readFileRecord,
  readRecipe,
  storeFileRecord,
  updateRecipe as updateStoredRecipe,
} from "./repository";
import type {
  DesignCatalog,
  DesignFileResult,
  DesignMutationResult,
  DesignRecipe,
  DesignRecipeSummary,
  DesignStatus,
  Env,
  JsonObject,
} from "./types";
import type {
  DesignImportInput,
  DesignUploadInput,
} from "./schemas";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const CONTENT_TYPE = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/i;

function bytesFromBase64(value: string): Uint8Array {
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new DesignError("invalid_base64", "the Design asset is not valid base64");
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes));
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function validateFile(contentType: string, bytes: Uint8Array): void {
  if (!CONTENT_TYPE.test(contentType)) {
    throw new DesignError("invalid_asset_type", "the Design asset type is invalid");
  }
  if (bytes.byteLength < 1 || bytes.byteLength > MAX_FILE_BYTES) {
    throw new DesignError("invalid_asset_size", "the Design asset size is invalid");
  }
}

function objectKey(recipeId: string, digest: string, path: string): string {
  return `recipes/${recipeId}/${digest}/${path}`;
}

export class DesignService {
  constructor(private readonly env: Pick<Env, "DB" | "ASSETS">) {}

  catalog(): Promise<DesignCatalog> {
    return readCatalog(this.env.DB);
  }

  listRecipes(options: {
    status?: DesignStatus | undefined;
    format?: string | undefined;
    limit: number;
  }): Promise<DesignRecipeSummary[]> {
    return listRecipes(this.env.DB, options);
  }

  readRecipe(id: string): Promise<DesignRecipe | null> {
    return readRecipe(this.env.DB, id);
  }

  createRecipe(metadata: JsonObject): Promise<DesignMutationResult> {
    return createRecipe(this.env.DB, metadata);
  }

  updateRecipe(
    id: string,
    expectedRevision: number,
    metadata: JsonObject,
  ): Promise<DesignMutationResult> {
    return updateStoredRecipe(this.env.DB, id, expectedRevision, metadata);
  }

  async importRecipe(input: DesignImportInput): Promise<DesignMutationResult> {
    const stored = [];
    for (const file of input.files) {
      const bytes = bytesFromBase64(file.base64);
      validateFile(file.content_type, bytes);
      const digest = await sha256(bytes);
      if (digest !== file.sha256) {
        throw new DesignError("asset_hash_mismatch", "a Design asset hash differs", 400, {
          path: file.path,
        });
      }
      const key = objectKey(input.recipe.id, digest, file.path);
      await this.env.ASSETS.put(key, bytes, {
        httpMetadata: { contentType: file.content_type },
        customMetadata: { sha256: digest },
      });
      stored.push({
        path: file.path,
        objectKey: key,
        contentType: file.content_type,
        byteSize: bytes.byteLength,
        sha256: digest,
      });
    }
    return importRecipe(
      this.env.DB,
      input.library,
      input.patterns,
      input.recipe,
      stored,
    );
  }

  async uploadFile(input: DesignUploadInput): Promise<DesignFileResult> {
    const bytes = bytesFromBase64(input.base64);
    validateFile(input.content_type, bytes);
    const digest = await sha256(bytes);
    const key = objectKey(input.id, digest, input.path);
    await this.env.ASSETS.put(key, bytes, {
      httpMetadata: { contentType: input.content_type },
      customMetadata: { sha256: digest },
    });
    const file = await storeFileRecord(
      this.env.DB,
      input.id,
      {
        path: input.path,
        objectKey: key,
        contentType: input.content_type,
        byteSize: bytes.byteLength,
        sha256: digest,
      },
      input.expected_file_revision,
    );
    return { status: "stored", recipeId: input.id, file };
  }

  async readFile(
    id: string,
    path: string,
  ): Promise<{ record: NonNullable<Awaited<ReturnType<typeof readFileRecord>>>; object: R2ObjectBody } | null> {
    const record = await readFileRecord(this.env.DB, id, path);
    if (!record) return null;
    const object = await this.env.ASSETS.get(record.object_key);
    if (!object) {
      throw new DesignError("asset_missing", "the Design asset bytes are missing", 500, {
        id,
        path,
      });
    }
    return { record, object };
  }
}
