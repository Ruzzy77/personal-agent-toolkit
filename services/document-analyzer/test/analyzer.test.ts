import { zipSync, strToU8 } from "fflate";
import { describe, expect, it } from "vitest";

import { canonicalJson, sha256 } from "../src/contract";
import type { AnalysisResult, FormatId } from "../src/types";
import worker from "../src/worker";

async function requestFor(
  bytes: Uint8Array,
  format: FormatId,
  digest?: string,
): Promise<Request> {
  return new Request("https://analyzer.internal/v1/analyze", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Analysis-Job": "analysis:test",
      "X-Input-Sha256": digest ?? await sha256(bytes),
      "X-Format-Id": format,
      "X-Source-Size": String(bytes.byteLength),
      "X-Owner-Id": "owner_test",
      "X-Device-Id": "device_test",
    },
    body: Uint8Array.from(bytes).buffer,
  });
}

describe("remote document analysis", () => {
  it("returns an identity-bound AnalysisResult for text", async () => {
    const bytes = new TextEncoder().encode("First paragraph.\n\nSecond paragraph.");
    const response = await worker.fetch(await requestFor(bytes, "txt"));
    const result = (await response.json()) as AnalysisResult;

    expect(response.status).toBe(200);
    expect(result.schema_version).toBe("document-files.analysis-result.v1");
    expect(result.input.sha256).toBe(await sha256(bytes));
    expect(result.extraction.units.map((unit) => unit.content)).toEqual([
      "First paragraph.",
      "Second paragraph.",
    ]);
    const { manifest_hash: manifestHash, ...manifest } = result.extraction;
    expect(manifestHash).toBe(
      await sha256(new TextEncoder().encode(canonicalJson(manifest))),
    );
  });

  it("extracts paragraph and table-cell text from a ZIP-based document", async () => {
    const bytes = zipSync({
      "[Content_Types].xml": strToU8("<Types/>") ,
      "word/document.xml": strToU8(`
        <w:document xmlns:w="urn:w">
          <w:body>
            <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          </w:body>
        </w:document>
      `),
    });
    const response = await worker.fetch(await requestFor(bytes, "docx"));
    const result = (await response.json()) as AnalysisResult;

    expect(response.status).toBe(200);
    expect(result.extraction.units.map((unit) => [unit.unit_type, unit.content])).toEqual([
      ["heading", "Title"],
      ["table_cell", "Cell"],
    ]);
    expect(result.extraction.completeness).toBe("partial");
  });

  it("rejects bytes that do not match the declared digest", async () => {
    const bytes = new TextEncoder().encode("private bytes");
    const response = await worker.fetch(await requestFor(bytes, "txt", "0".repeat(64)));
    const payload = (await response.json()) as { error: { code: string } };

    expect(response.status).toBe(400);
    expect(payload.error.code).toBe("analysis_identity_mismatch");
  });
});
