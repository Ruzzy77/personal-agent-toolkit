import type { McpServer } from "@modelcontextprotocol/server";
import { mcpTextError } from "@personal-agent/remote-runtime";
import { z } from "zod";

import registry from "../../../products.json";
import type { Env, Principal } from "./types";

/**
 * Product switches for the unified toolkit connection. One registration and one
 * authorization serve every product, and the owner narrows what that connection
 * exposes here instead of connecting each product separately. A disabled product
 * is left out of `tools/list` for the next connection; the per-product endpoints
 * a client registered on its own are unaffected.
 */

export const TOOLKIT_PRODUCTS = [
  "sense",
  "corpus",
  "hypes",
  "journal",
  "library",
  "design",
  "host",
] as const;

export type ToolkitProduct = (typeof TOOLKIT_PRODUCTS)[number];

export const TOOLKIT_CONTROL_TOOLS = [
  "toolkit_products",
  "toolkit_products_set",
] as const;

const productName = z.enum(TOOLKIT_PRODUCTS);
const controlOutputSchema = z.looseObject({});

/** Products the owner switched off. A missing table means nothing is off yet. */
export async function disabledProducts(
  env: Env,
  ownerId: string,
): Promise<Set<ToolkitProduct>> {
  try {
    const result = await env.STATE_DB.prepare(
      "SELECT product FROM toolkit_product_state WHERE owner_id = ? AND enabled = 0",
    )
      .bind(ownerId)
      .all<{ product: string }>();
    const disabled = new Set<ToolkitProduct>();
    for (const row of result.results ?? []) {
      if ((TOOLKIT_PRODUCTS as readonly string[]).includes(row.product)) {
        disabled.add(row.product as ToolkitProduct);
      }
    }
    return disabled;
  } catch {
    return new Set<ToolkitProduct>();
  }
}

function toolCount(product: ToolkitProduct): number {
  return registry.products[product].mcp.tools.length;
}

function state(disabled: Set<ToolkitProduct>) {
  return {
    products: TOOLKIT_PRODUCTS.map((product) => ({
      product,
      enabled: !disabled.has(product),
      tools: toolCount(product),
    })),
    exposed_tools:
      TOOLKIT_CONTROL_TOOLS.length +
      TOOLKIT_PRODUCTS.filter((product) => !disabled.has(product)).reduce(
        (total, product) => total + toolCount(product),
        0,
      ),
  };
}

export function registerToolkitProductTools(
  server: McpServer,
  env: Env,
  principal: Principal,
  disabled: Set<ToolkitProduct>,
): void {
  server.registerTool(
    "toolkit_products",
    {
      title: "Toolkit products",
      description:
        "List the products this one toolkit connection can expose, whether each is switched on, and how many tools it contributes.",
      inputSchema: z.object({}).strict(),
      outputSchema: controlOutputSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async () => ({
      content: [],
      structuredContent: { ok: true as const, result: state(disabled) },
    }),
  );

  server.registerTool(
    "toolkit_products_set",
    {
      title: "Switch a toolkit product",
      description:
        "Switch one product on or off for this toolkit connection. The new tool list applies the next time a client connects; authorization is unchanged.",
      inputSchema: z
        .object({ product: productName, enabled: z.boolean() })
        .strict(),
      outputSchema: controlOutputSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (input) => {
      const { product, enabled } = input as {
        product: ToolkitProduct;
        enabled: boolean;
      };
      try {
        await env.STATE_DB.prepare(
          `INSERT INTO toolkit_product_state(owner_id, product, enabled, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(owner_id, product) DO UPDATE SET
             enabled = excluded.enabled, updated_at = excluded.updated_at`,
        )
          .bind(
            principal.ownerId,
            product,
            enabled ? 1 : 0,
            new Date().toISOString(),
          )
          .run();
      } catch {
        return mcpTextError({
          ok: false as const,
          error: {
            code: "product_switch_unavailable",
            message: "the product switch could not be saved",
          },
        });
      }
      const next = new Set(disabled);
      if (enabled) next.delete(product);
      else next.add(product);
      return {
        content: [],
        structuredContent: {
          ok: true as const,
          result: {
            ...state(next),
            applies: "next client connection",
          },
        },
      };
    },
  );
}
