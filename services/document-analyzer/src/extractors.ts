import { Parser } from "htmlparser2";
import { extractText as extractPdfText, getDocumentProxy } from "unpdf";
import {
  parseHwp,
} from "@ssabrojs/hwpxjs";

import { decodeXml, naturalPartOrder, unzipDocument } from "./archive";
import { AnalyzerError } from "./errors";
import type {
  Coverage,
  ExtractedUnit,
  ExtractionDraft,
  ExtractionIssue,
  FormatId,
} from "./types";
import {
  completeCoverage,
  issue,
  normalizeText,
  noTextIssue,
  UnitCollector,
} from "./units";

const decoder = new TextDecoder("utf-8", { fatal: false });
type HwpDocument = ReturnType<typeof parseHwp>;
type HwpParagraph = HwpDocument["sections"][number]["paragraphs"][number];
type HwpControl = HwpParagraph["controls"][number];

function localName(name: string): string {
  return name.includes(":") ? name.slice(name.lastIndexOf(":") + 1) : name;
}

function attribute(attributes: Record<string, string>, name: string): string | undefined {
  for (const [key, value] of Object.entries(attributes)) {
    if (localName(key) === name) return value;
  }
  return undefined;
}

function finalDraft(
  collector: UnitCollector,
  options: {
    issues?: ExtractionIssue[];
    coverage?: Partial<Coverage>;
    structuralUnitTypes: string[];
    preservesReadingOrder: boolean;
    adapterFamily: string;
  },
): ExtractionDraft {
  const issues = [...(options.issues ?? [])];
  const coverage = { ...completeCoverage(), ...(options.coverage ?? {}) };
  if (!collector.units.length) {
    issues.push(noTextIssue());
    coverage.text_content = "partial";
    coverage.structure = "partial";
  }
  return {
    units: collector.units,
    issues,
    coverage,
    structuralUnitTypes: options.structuralUnitTypes,
    preservesReadingOrder: options.preservesReadingOrder,
    adapterFamily: options.adapterFamily,
  };
}

function extractPlainText(bytes: Uint8Array): ExtractionDraft {
  const collector = new UnitCollector();
  const text = decoder.decode(bytes);
  for (const [index, paragraph] of text.split(/\n\s*\n/).entries()) {
    collector.add("paragraph", { paragraph: index + 1 }, paragraph);
  }
  return finalDraft(collector, {
    structuralUnitTypes: ["paragraph"],
    preservesReadingOrder: true,
    adapterFamily: "text",
  });
}

function extractMarkdown(bytes: Uint8Array): ExtractionDraft {
  const collector = new UnitCollector();
  const lines = decoder.decode(bytes).replace(/\r\n?/g, "\n").split("\n");
  const headings: string[] = [];
  let paragraph: string[] = [];
  let paragraphStart = 1;

  const flush = (lineEnd: number) => {
    if (!paragraph.length) return;
    collector.add(
      "paragraph",
      {
        heading_path: [...headings],
        line_start: paragraphStart,
        line_end: lineEnd,
      },
      paragraph.join("\n"),
    );
    paragraph = [];
  };

  for (const [index, line] of lines.entries()) {
    const lineNumber = index + 1;
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading) {
      flush(lineNumber - 1);
      const level = heading[1]!.length;
      const title = normalizeText(heading[2]!);
      headings.splice(level - 1);
      headings[level - 1] = title;
      collector.add(
        "heading",
        {
          heading_path: [...headings],
          level,
          line_start: lineNumber,
          line_end: lineNumber,
        },
        title,
      );
      paragraphStart = lineNumber + 1;
    } else if (!line.trim()) {
      flush(lineNumber - 1);
      paragraphStart = lineNumber + 1;
    } else {
      if (!paragraph.length) paragraphStart = lineNumber;
      paragraph.push(line);
    }
  }
  flush(lines.length);
  return finalDraft(collector, {
    structuralUnitTypes: ["heading", "paragraph"],
    preservesReadingOrder: true,
    adapterFamily: "markdown",
  });
}

function extractHtml(bytes: Uint8Array): ExtractionDraft {
  const collector = new UnitCollector();
  const blocks = new Set(["p", "li", "blockquote", "pre", "td", "th", "figcaption", "title"]);
  const headings = new Set(["h1", "h2", "h3", "h4", "h5", "h6"]);
  const suppressed = new Set(["script", "style", "noscript"]);
  const voidTags = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  const headingStack: string[] = [];
  const counts = new Map<string, number>();
  const looseText: string[] = [];
  let suppressedDepth = 0;
  let current: { tag: string; depth: number; parts: string[] } | null = null;
  let hasImages = false;

  const finish = () => {
    if (!current) return;
    const capture = current;
    current = null;
    const content = normalizeText(capture.parts.join(" "));
    if (!content) return;
    const count = (counts.get(capture.tag) ?? 0) + 1;
    counts.set(capture.tag, count);
    if (headings.has(capture.tag)) {
      const level = Number(capture.tag.slice(1));
      headingStack.splice(level - 1);
      headingStack[level - 1] = content;
      collector.add("heading", { heading_path: [...headingStack], level, occurrence: count }, content);
    } else {
      collector.add(
        capture.tag === "td" || capture.tag === "th" ? "table" : "paragraph",
        { tag: capture.tag, occurrence: count, heading_path: [...headingStack] },
        content,
      );
    }
  };

  const parser = new Parser(
    {
      onopentag(name) {
        const tag = localName(name).toLowerCase();
        if (suppressed.has(tag)) {
          suppressedDepth += 1;
          return;
        }
        if (suppressedDepth) return;
        if (tag === "img") hasImages = true;
        if (current) {
          if (tag === "br") current.parts.push("\n");
          if (!voidTags.has(tag)) current.depth += 1;
        } else if (blocks.has(tag) || headings.has(tag)) {
          current = { tag, depth: 1, parts: [] };
        }
      },
      ontext(text) {
        if (suppressedDepth) return;
        if (current) current.parts.push(text);
        else if (text.trim()) looseText.push(text);
      },
      onclosetag(name) {
        const tag = localName(name).toLowerCase();
        if (suppressed.has(tag) && suppressedDepth) {
          suppressedDepth -= 1;
          return;
        }
        if (suppressedDepth || !current) return;
        current.depth -= 1;
        if (current.depth === 0) finish();
      },
    },
    { decodeEntities: true },
  );
  parser.end(decoder.decode(bytes));
  finish();
  if (!collector.units.length && looseText.length) {
    collector.add("paragraph", { scope: "visible_text_fallback" }, looseText.join(" "));
  }
  const issues: ExtractionIssue[] = [];
  const coverage: Partial<Coverage> = {};
  if (hasImages) {
    issues.push(
      issue(
        "remote_visual_content_unverified",
        "Stored HTML images were identified but not interpreted.",
        "observation",
        ["visual_content"],
        "info",
      ),
    );
    coverage.visual_content = "unverified";
  }
  return finalDraft(collector, {
    issues,
    coverage,
    structuralUnitTypes: ["heading", "paragraph", "table"],
    preservesReadingOrder: true,
    adapterFamily: "html",
  });
}

interface ParagraphCapture {
  parts: string[];
  style?: string;
  list: boolean;
}

function extractDocx(bytes: Uint8Array): ExtractionDraft {
  const archive = unzipDocument(bytes);
  const collector = new UnitCollector();
  const parts = Object.keys(archive)
    .filter((name) => /^word\/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$/i.test(name))
    .sort((left, right) => {
      const rank = (value: string) => {
        if (/\/document\.xml$/i.test(value)) return 0;
        if (/\/header\d+\.xml$/i.test(value)) return 1;
        if (/\/footer\d+\.xml$/i.test(value)) return 2;
        if (/\/footnotes\.xml$/i.test(value)) return 3;
        if (/\/endnotes\.xml$/i.test(value)) return 4;
        return 5;
      };
      return rank(left) - rank(right) || naturalPartOrder(left, right);
    });
  if (!parts.some((part) => /^word\/document\.xml$/i.test(part))) {
    throw new AnalyzerError("invalid_document", "DOCX main document XML is missing", 422);
  }

  for (const part of parts) {
    const partLabel = part.replace(/^word\//i, "").replace(/\.xml$/i, "");
    let paragraph: ParagraphCapture | null = null;
    let captureText = false;
    let paragraphIndex = 0;
    let tableIndex = 0;
    let rowIndex = 0;
    let columnIndex = 0;
    let cell: { table: number; row: number; column: number; paragraphs: string[] } | null = null;
    let tableDepth = 0;

    const finishParagraph = () => {
      if (!paragraph) return;
      paragraphIndex += 1;
      const content = paragraph.parts.join("");
      if (cell) {
        if (normalizeText(content)) cell.paragraphs.push(content);
      } else {
        let unitType = "paragraph";
        const structure: Record<string, unknown> = { part: partLabel, paragraph: paragraphIndex };
        if (partLabel.startsWith("header")) unitType = "header";
        else if (partLabel.startsWith("footer")) unitType = "footer";
        else if (partLabel === "footnotes") unitType = "footnote";
        else if (partLabel === "endnotes") unitType = "endnote";
        else if (partLabel === "comments") unitType = "comment";
        else if (paragraph.style && /^heading\s*[1-6]$/i.test(paragraph.style)) {
          unitType = "heading";
          structure.level = Number(paragraph.style.match(/[1-6]$/)?.[0] ?? 1);
        } else if (paragraph.list) unitType = "list_item";
        collector.add(unitType, structure, content);
      }
      paragraph = null;
    };

    const parser = new Parser(
      {
        onopentag(name, attributes) {
          const tag = localName(name);
          if (tag === "tbl") {
            tableDepth += 1;
            if (tableDepth === 1) {
              tableIndex += 1;
              rowIndex = 0;
            }
          } else if (tag === "tr" && tableDepth === 1) {
            rowIndex += 1;
            columnIndex = 0;
          } else if (tag === "tc" && tableDepth === 1) {
            columnIndex += 1;
            cell = { table: tableIndex, row: rowIndex, column: columnIndex, paragraphs: [] };
          } else if (tag === "p") {
            paragraph = { parts: [], list: false };
          } else if (tag === "t" && paragraph) {
            captureText = true;
          } else if ((tag === "tab" || tag === "br" || tag === "cr") && paragraph) {
            paragraph.parts.push(tag === "tab" ? "\t" : "\n");
          } else if (tag === "pStyle" && paragraph) {
            paragraph.style = attribute(attributes, "val");
          } else if (tag === "numPr" && paragraph) {
            paragraph.list = true;
          }
        },
        ontext(text) {
          if (captureText && paragraph) paragraph.parts.push(text);
        },
        onclosetag(name) {
          const tag = localName(name);
          if (tag === "t") captureText = false;
          else if (tag === "p") finishParagraph();
          else if (tag === "tc" && cell && tableDepth === 1) {
            collector.add(
              "table_cell",
              { part: partLabel, table: cell.table, row: cell.row, column: cell.column },
              cell.paragraphs.join("\n"),
              [],
              true,
            );
            cell = null;
          } else if (tag === "tbl") tableDepth -= 1;
        },
      },
      { xmlMode: true, decodeEntities: true },
    );
    parser.end(decodeXml(archive[part]!));
  }

  const hasImages = Object.keys(archive).some((name) => /^word\/media\//i.test(name));
  const issues = [
    issue(
      "remote_office_structure_partial",
      "Remote extraction preserves stored text and basic document order but not every Word layout object.",
      "structure_gap",
      ["structure"],
    ),
    issue(
      "reading_order_unverified",
      "Floating Word objects may not follow the extracted package order.",
      "reading_order_unverified",
      ["reading_order"],
    ),
  ];
  if (hasImages) {
    issues.push(
      issue(
        "remote_visual_content_uninterpreted",
        "Embedded Word images were identified but not interpreted.",
        "observation",
        ["visual_content"],
        "info",
      ),
    );
  }
  return finalDraft(collector, {
    issues,
    coverage: {
      structure: "partial",
      reading_order: "unverified",
      visual_content: hasImages ? "unverified" : "not_applicable",
    },
    structuralUnitTypes: [
      "heading",
      "paragraph",
      "list_item",
      "table_cell",
      "header",
      "footer",
      "footnote",
      "endnote",
      "comment",
    ],
    preservesReadingOrder: false,
    adapterFamily: "docx",
  });
}

function collectXmlText(xml: string, textTags = new Set(["t"])): string[] {
  const values: string[] = [];
  let capture = false;
  let parts: string[] = [];
  const parser = new Parser(
    {
      onopentag(name) {
        if (textTags.has(localName(name))) {
          capture = true;
          parts = [];
        }
      },
      ontext(text) {
        if (capture) parts.push(text);
      },
      onclosetag(name) {
        if (capture && textTags.has(localName(name))) {
          const value = normalizeText(parts.join(""));
          if (value) values.push(value);
          capture = false;
          parts = [];
        }
      },
    },
    { xmlMode: true, decodeEntities: true },
  );
  parser.end(xml);
  return values;
}

function extractPptx(bytes: Uint8Array): ExtractionDraft {
  const archive = unzipDocument(bytes);
  const collector = new UnitCollector();
  const slides = Object.keys(archive)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/i.test(name))
    .sort(naturalPartOrder);
  if (!slides.length) throw new AnalyzerError("invalid_document", "PPTX has no slide XML", 422);
  for (const [index, part] of slides.entries()) {
    collector.add(
      "slide_text",
      { slide: index + 1 },
      collectXmlText(decodeXml(archive[part]!)).join("\n"),
      [],
      true,
    );
  }
  const notes = Object.keys(archive)
    .filter((name) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/i.test(name))
    .sort(naturalPartOrder);
  for (const [index, part] of notes.entries()) {
    collector.add(
      "speaker_notes",
      { slide: index + 1 },
      collectXmlText(decodeXml(archive[part]!)).join("\n"),
    );
  }
  const charts = Object.keys(archive)
    .filter((name) => /^ppt\/charts\/chart\d+\.xml$/i.test(name))
    .sort(naturalPartOrder);
  for (const [index, part] of charts.entries()) {
    collector.add(
      "chart_data",
      { chart: index + 1 },
      collectXmlText(decodeXml(archive[part]!), new Set(["v"])).join("\n"),
    );
  }
  const hasImages = Object.keys(archive).some((name) => /^ppt\/media\//i.test(name));
  const issues = [
    issue(
      "remote_office_structure_partial",
      "Remote extraction preserves slide, notes, and stored chart text but not every presentation object.",
      "structure_gap",
      ["structure"],
    ),
    issue(
      "reading_order_unverified",
      "Presentation drawing order is not a verified visual reading order.",
      "reading_order_unverified",
      ["reading_order"],
    ),
  ];
  if (hasImages) {
    issues.push(
      issue(
        "remote_visual_content_uninterpreted",
        "Embedded presentation images were identified but not interpreted.",
        "observation",
        ["visual_content"],
        "info",
      ),
    );
  }
  return finalDraft(collector, {
    issues,
    coverage: {
      structure: "partial",
      reading_order: "unverified",
      visual_content: hasImages ? "unverified" : "not_applicable",
    },
    structuralUnitTypes: ["slide_text", "speaker_notes", "chart_data"],
    preservesReadingOrder: false,
    adapterFamily: "pptx",
  });
}

function sharedStrings(archive: Record<string, Uint8Array>): string[] {
  const part = Object.keys(archive).find((name) => /^xl\/sharedStrings\.xml$/i.test(name));
  if (!part) return [];
  const values: string[] = [];
  let inItem = false;
  let captureText = false;
  let parts: string[] = [];
  const parser = new Parser(
    {
      onopentag(name) {
        const tag = localName(name);
        if (tag === "si") {
          inItem = true;
          parts = [];
        } else if (tag === "t" && inItem) captureText = true;
      },
      ontext(text) {
        if (captureText) parts.push(text);
      },
      onclosetag(name) {
        const tag = localName(name);
        if (tag === "t") captureText = false;
        else if (tag === "si") {
          values.push(parts.join(""));
          inItem = false;
        }
      },
    },
    { xmlMode: true, decodeEntities: true },
  );
  parser.end(decodeXml(archive[part]!));
  return values;
}

function workbookSheetNames(archive: Record<string, Uint8Array>): string[] {
  const part = Object.keys(archive).find((name) => /^xl\/workbook\.xml$/i.test(name));
  if (!part) return [];
  const names: string[] = [];
  const parser = new Parser(
    {
      onopentag(name, attributes) {
        if (localName(name) === "sheet") names.push(attribute(attributes, "name") ?? `Sheet ${names.length + 1}`);
      },
    },
    { xmlMode: true, decodeEntities: true },
  );
  parser.end(decodeXml(archive[part]!));
  return names;
}

function extractXlsx(bytes: Uint8Array): ExtractionDraft {
  const archive = unzipDocument(bytes);
  const collector = new UnitCollector();
  const strings = sharedStrings(archive);
  const names = workbookSheetNames(archive);
  const sheets = Object.keys(archive)
    .filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/i.test(name))
    .sort(naturalPartOrder);
  if (!sheets.length) throw new AnalyzerError("invalid_document", "XLSX has no worksheet XML", 422);

  for (const [sheetIndex, part] of sheets.entries()) {
    const sheet = names[sheetIndex] ?? `Sheet ${sheetIndex + 1}`;
    let cell: {
      reference: string;
      kind: string;
      value: string[];
      inline: string[];
      formula: string[];
    } | null = null;
    let capture: "value" | "inline" | "formula" | null = null;
    const parser = new Parser(
      {
        onopentag(name, attributes) {
          const tag = localName(name);
          if (tag === "c") {
            cell = {
              reference: attribute(attributes, "r") ?? "unknown",
              kind: attribute(attributes, "t") ?? "number",
              value: [],
              inline: [],
              formula: [],
            };
          } else if (cell && tag === "v") capture = "value";
          else if (cell && tag === "t") capture = "inline";
          else if (cell && tag === "f") capture = "formula";
        },
        ontext(text) {
          if (!cell || !capture) return;
          cell[capture].push(text);
        },
        onclosetag(name) {
          const tag = localName(name);
          if (tag === "v" || tag === "t" || tag === "f") capture = null;
          else if (tag === "c" && cell) {
            const raw = cell.value.join("");
            let content = cell.inline.join("");
            let valueKind = cell.kind;
            if (cell.kind === "s") {
              content = strings[Number(raw)] ?? "";
              valueKind = "shared_string";
            } else if (cell.kind === "b") {
              content = raw === "1" ? "TRUE" : "FALSE";
              valueKind = "boolean";
            } else if (!content) content = raw;
            const formula = cell.formula.join("");
            if (!content && formula) content = `=${formula}`;
            collector.add(
              "sheet_cell",
              {
                sheet,
                cell: cell.reference,
                value_kind: valueKind,
                ...(formula ? { formula } : {}),
              },
              content,
            );
            cell = null;
          }
        },
      },
      { xmlMode: true, decodeEntities: true },
    );
    parser.end(decodeXml(archive[part]!));
  }

  const issues = [
    issue(
      "remote_spreadsheet_formatting_unverified",
      "Stored cell values and formulas were extracted without evaluating formulas or applying display formats.",
      "observation",
      ["structure"],
      "info",
    ),
  ];
  return finalDraft(collector, {
    issues,
    coverage: { structure: "unverified" },
    structuralUnitTypes: ["sheet_cell"],
    preservesReadingOrder: true,
    adapterFamily: "xlsx",
  });
}

function extractHwpx(bytes: Uint8Array): ExtractionDraft {
  const archive = unzipDocument(bytes);
  const collector = new UnitCollector();
  const sections = Object.keys(archive)
    .filter((name) => /^(?:Contents\/)?section\d+\.xml$/i.test(name))
    .sort(naturalPartOrder);
  if (!sections.length) throw new AnalyzerError("invalid_document", "HWPX has no section XML", 422);
  for (const [sectionIndex, part] of sections.entries()) {
    let paragraph: string[] | null = null;
    let captureText = false;
    let paragraphIndex = 0;
    let tableIndex = 0;
    let rowIndex = 0;
    let columnIndex = 0;
    let tableDepth = 0;
    let cell: { table: number; row: number; column: number; paragraphs: string[] } | null = null;
    const finishParagraph = () => {
      if (!paragraph) return;
      paragraphIndex += 1;
      const content = paragraph.join("");
      if (cell) {
        if (normalizeText(content)) cell.paragraphs.push(content);
      } else {
        collector.add(
          "section_paragraph",
          { section: sectionIndex + 1, paragraph: paragraphIndex },
          content,
        );
      }
      paragraph = null;
    };
    const parser = new Parser(
      {
        onopentag(name) {
          const tag = localName(name);
          if (tag === "tbl") {
            tableDepth += 1;
            if (tableDepth === 1) {
              tableIndex += 1;
              rowIndex = 0;
            }
          } else if (tag === "tr" && tableDepth === 1) {
            rowIndex += 1;
            columnIndex = 0;
          } else if (tag === "tc" && tableDepth === 1) {
            columnIndex += 1;
            cell = { table: tableIndex, row: rowIndex, column: columnIndex, paragraphs: [] };
          } else if (tag === "p") paragraph = [];
          else if (tag === "t" && paragraph) captureText = true;
          else if ((tag === "tab" || tag === "lineBreak") && paragraph) paragraph.push(tag === "tab" ? "\t" : "\n");
        },
        ontext(text) {
          if (captureText && paragraph) paragraph.push(text);
        },
        onclosetag(name) {
          const tag = localName(name);
          if (tag === "t") captureText = false;
          else if (tag === "p") finishParagraph();
          else if (tag === "tc" && cell && tableDepth === 1) {
            collector.add(
              "table_cell",
              {
                section: sectionIndex + 1,
                table: cell.table,
                row: cell.row,
                column: cell.column,
              },
              cell.paragraphs.join("\n"),
              [],
              true,
            );
            cell = null;
          } else if (tag === "tbl") tableDepth -= 1;
        },
      },
      { xmlMode: true, decodeEntities: true },
    );
    parser.end(decodeXml(archive[part]!));
  }
  const hasImages = Object.keys(archive).some((name) => /^BinData\//i.test(name));
  const issues = [
    issue(
      "remote_hwpx_structure_partial",
      "Remote extraction preserves section paragraphs and table cells but not every HWPX layout object.",
      "structure_gap",
      ["structure"],
    ),
  ];
  if (hasImages) {
    issues.push(
      issue(
        "remote_visual_content_uninterpreted",
        "Embedded HWPX images were identified but not interpreted.",
        "observation",
        ["visual_content"],
        "info",
      ),
    );
  }
  return finalDraft(collector, {
    issues,
    coverage: {
      structure: "partial",
      visual_content: hasImages ? "unverified" : "not_applicable",
    },
    structuralUnitTypes: ["section_paragraph", "table_cell"],
    preservesReadingOrder: true,
    adapterFamily: "hwpx",
  });
}

function paragraphText(paragraphs: HwpParagraph[]): string {
  return normalizeText(paragraphs.map((paragraph) => paragraph.text).join("\n"));
}

function extractHwp(bytes: Uint8Array): ExtractionDraft {
  const collector = new UnitCollector();
  let document;
  try {
    document = parseHwp(bytes);
  } catch {
    throw new AnalyzerError(
      "invalid_or_unsupported_hwp",
      "the HWP document is invalid, encrypted, or unsupported by the remote runtime",
      422,
    );
  }
  let hasImages = document.binData.size > 0;
  const processControls = (
    controls: HwpControl[],
    base: Record<string, unknown>,
  ) => {
    let tableIndex = 0;
    for (const control of controls) {
      if (control.kind === "table") {
        tableIndex += 1;
        for (const cell of control.cells) {
          collector.add(
            "table_cell",
            {
              ...base,
              table: tableIndex,
              row: cell.row + 1,
              column: cell.col + 1,
              row_span: cell.rowSpan,
              column_span: cell.colSpan,
            },
            paragraphText(cell.paragraphs),
            [],
            true,
          );
        }
      } else if (control.kind === "header" || control.kind === "footer" || control.kind === "footnote") {
        collector.add(control.kind, { ...base, control: control.kind }, paragraphText(control.paragraphs));
      } else if (control.kind === "field" && control.command) {
        collector.add("field", { ...base, control: control.ctrlId }, control.command);
      } else if (control.kind === "equation" && control.script) {
        collector.add("embedded_object", { ...base, control: "equation" }, control.script);
      } else if (control.kind === "picture") {
        hasImages = true;
      }
    }
  };

  for (const [sectionIndex, section] of document.sections.entries()) {
    for (const [paragraphIndex, paragraph] of section.paragraphs.entries()) {
      const base = { section: sectionIndex + 1, paragraph: paragraphIndex + 1 };
      collector.add("section_paragraph", base, paragraph.text);
      processControls(paragraph.controls, base);
    }
  }
  const issues = [
    issue(
      "remote_hwp_structure_partial",
      "Remote extraction preserves stored HWP paragraphs and common controls but does not render native layout.",
      "structure_gap",
      ["structure"],
    ),
  ];
  if (hasImages) {
    issues.push(
      issue(
        "remote_visual_content_uninterpreted",
        "Embedded HWP images were identified but not interpreted.",
        "observation",
        ["visual_content"],
        "info",
      ),
    );
  }
  return finalDraft(collector, {
    issues,
    coverage: {
      structure: "partial",
      visual_content: hasImages ? "unverified" : "not_applicable",
    },
    structuralUnitTypes: [
      "section_paragraph",
      "table_cell",
      "header",
      "footer",
      "footnote",
      "field",
      "embedded_object",
    ],
    preservesReadingOrder: true,
    adapterFamily: "hwp",
  });
}

async function extractPdf(bytes: Uint8Array): Promise<ExtractionDraft> {
  const collector = new UnitCollector();
  const issues: ExtractionIssue[] = [
    issue(
      "reading_order_unverified",
      "PDF text order is not a verified visual reading order.",
      "reading_order_unverified",
      ["reading_order"],
    ),
  ];
  let hasEmptyPage = false;
  try {
    const document = await getDocumentProxy(bytes);
    try {
      const extracted = await extractPdfText(document, { mergePages: false });
      const pages = Array.isArray(extracted.text) ? extracted.text : [extracted.text];
      for (const [index, page] of pages.entries()) {
        const empty = !normalizeText(page);
        hasEmptyPage ||= empty;
        collector.add(
          "page",
          { page: index + 1 },
          page,
          empty
            ? [
                issue(
                  "pdf_page_without_text",
                  "A PDF page has no extractable stored text and may require OCR.",
                  "content_gap",
                  ["text_content"],
                ),
              ]
            : [],
          true,
        );
      }
    } finally {
      await (document as unknown as { destroy(): Promise<void> }).destroy();
    }
  } catch (error) {
    if (error instanceof AnalyzerError) throw error;
    throw new AnalyzerError(
      "invalid_or_encrypted_pdf",
      "the PDF is invalid, encrypted, or unsupported by the remote runtime",
      422,
    );
  }
  return finalDraft(collector, {
    issues,
    coverage: {
      text_content: hasEmptyPage ? "partial" : "complete",
      visual_content: "unverified",
      reading_order: "unverified",
    },
    structuralUnitTypes: ["page"],
    preservesReadingOrder: false,
    adapterFamily: "pdf",
  });
}

export async function extractDocument(
  formatId: FormatId,
  bytes: Uint8Array,
): Promise<ExtractionDraft> {
  switch (formatId) {
    case "txt":
      return extractPlainText(bytes);
    case "md":
    case "markdown":
      return extractMarkdown(bytes);
    case "html":
    case "htm":
      return extractHtml(bytes);
    case "docx":
      return extractDocx(bytes);
    case "pptx":
      return extractPptx(bytes);
    case "xlsx":
      return extractXlsx(bytes);
    case "hwpx":
      return extractHwpx(bytes);
    case "hwp":
      return extractHwp(bytes);
    case "pdf":
      return extractPdf(bytes);
  }
}
