import registry from "../../../products.json";
import type { ResourceKind } from "./types";

export type McpSurface = {
  name: string;
  version: string;
  tools: readonly string[];
};
const productSurface = (product: "sense" | "corpus" | "hypes"): McpSurface => ({
  name: registry.products[product].mcp.surface_name,
  version: registry.products[product].mcp.surface_version,
  tools: registry.products[product].mcp.tools,
});
// The existing registry is the deployment manifest; tests compare it to the
// executable definitions, so neither transport maintains another tool list.
export const MCP_SURFACES = {
  sense: productSurface("sense"),
  corpus: productSurface("corpus"),
  hypes: productSurface("hypes"),
  toolkit: {
    name: registry.distributions.openai.mcp.surface_name,
    version: registry.distributions.openai.mcp.surface_version,
    tools: ["sense", "corpus", "hypes", "journal", "library", "design"].flatMap(
      (name) => registry.products[name as "sense"].mcp.tools,
    ),
  },
} satisfies Record<ResourceKind, McpSurface>;
