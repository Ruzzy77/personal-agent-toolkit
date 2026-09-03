import { unzipSync } from "fflate";

import { AnalyzerError } from "./errors";

const MAX_ARCHIVE_MEMBERS = 20_000;
const MAX_ARCHIVE_EXPANDED_BYTES = 64 * 1024 * 1024;
const MAX_XML_MEMBER_BYTES = 16 * 1024 * 1024;

function u16(view: DataView, offset: number): number {
  return view.getUint16(offset, true);
}

function u32(view: DataView, offset: number): number {
  return view.getUint32(offset, true);
}

function preflightZip(bytes: Uint8Array): void {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  const start = Math.max(0, bytes.byteLength - 65_557);
  for (let offset = bytes.byteLength - 22; offset >= start; offset -= 1) {
    if (u32(view, offset) === 0x06054b50) {
      eocd = offset;
      break;
    }
  }
  if (eocd < 0) throw new AnalyzerError("invalid_document", "invalid ZIP document", 422);
  const members = u16(view, eocd + 10);
  const directorySize = u32(view, eocd + 12);
  const directoryOffset = u32(view, eocd + 16);
  if (
    members > MAX_ARCHIVE_MEMBERS
    || directoryOffset + directorySize > bytes.byteLength
  ) {
    throw new AnalyzerError("archive_limit_exceeded", "document archive exceeds its safety limit", 413);
  }
  let offset = directoryOffset;
  let expanded = 0;
  for (let index = 0; index < members; index += 1) {
    if (offset + 46 > bytes.byteLength || u32(view, offset) !== 0x02014b50) {
      throw new AnalyzerError("invalid_document", "invalid ZIP directory", 422);
    }
    const size = u32(view, offset + 24);
    if (size === 0xffffffff) {
      throw new AnalyzerError("unsupported_archive", "ZIP64 documents are not supported remotely", 415);
    }
    expanded += size;
    if (expanded > MAX_ARCHIVE_EXPANDED_BYTES) {
      throw new AnalyzerError("archive_limit_exceeded", "document archive expands beyond its safety limit", 413);
    }
    offset += 46 + u16(view, offset + 28) + u16(view, offset + 30) + u16(view, offset + 32);
  }
}

export function unzipDocument(bytes: Uint8Array): Record<string, Uint8Array> {
  preflightZip(bytes);
  try {
    return unzipSync(bytes);
  } catch {
    throw new AnalyzerError("invalid_document", "the ZIP document could not be opened", 422);
  }
}

export function decodeXml(bytes: Uint8Array): string {
  if (bytes.byteLength > MAX_XML_MEMBER_BYTES) {
    throw new AnalyzerError("archive_limit_exceeded", "document XML exceeds its safety limit", 413);
  }
  let text: string;
  if (bytes[0] === 0xff && bytes[1] === 0xfe) {
    text = new TextDecoder("utf-16le", { fatal: false }).decode(bytes.subarray(2));
  } else if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    const swapped = bytes.subarray(2).slice();
    for (let index = 0; index + 1 < swapped.length; index += 2) {
      [swapped[index], swapped[index + 1]] = [swapped[index + 1]!, swapped[index]!];
    }
    text = new TextDecoder("utf-16le", { fatal: false }).decode(swapped);
  } else {
    text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  }
  if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(text)) {
    throw new AnalyzerError("unsafe_xml", "document XML contains a forbidden declaration", 422);
  }
  return text;
}

export function naturalPartOrder(left: string, right: string): number {
  const tokenize = (value: string) => value.split(/(\d+)/).map((token) => /^\d+$/.test(token) ? Number(token) : token);
  const a = tokenize(left);
  const b = tokenize(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if (a[index] === undefined) return -1;
    if (b[index] === undefined) return 1;
    if (a[index] === b[index]) continue;
    if (typeof a[index] === "number" && typeof b[index] === "number") {
      return (a[index] as number) - (b[index] as number);
    }
    return String(a[index]).localeCompare(String(b[index]), "en");
  }
  return 0;
}

