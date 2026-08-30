"""Native OOXML text in package order, with stable part and element locators.

No external relationships, linked workbooks, macros or field commands are opened.
XML order is not a claim about floating-object or slide visual reading order.
"""

from __future__ import annotations

import posixpath
import zipfile
from collections import Counter

from .errors import ExtractionError
from .extractors import (
    ExtractionResult,
    UnitDraft,
    _bounded_unit,
    _local_name,
    _preflight_zip,
    _safe_archive_xml_root,
    normalize_text,
)

DOCX_UNITS = (
    "heading",
    "paragraph",
    "list_item",
    "table",
    "table_cell",
    "header",
    "footer",
    "footnote",
    "endnote",
    "comment",
    "embedded_object",
    "field",
)
PPTX_UNITS = (
    "slide_text",
    "speaker_notes",
    "table",
    "table_cell",
    "chart_data",
    "diagram_text",
    "embedded_object",
)


def _attr(node, name, default=None):
    return next((v for k, v in node.attrib.items() if _local_name(k) == name), default)


def _child(node, name):
    return next((c for c in node if _local_name(c.tag) == name), None)


def _desc(node, name):
    return (c for c in node.iter() if _local_name(c.tag) == name)


def _integer(value, default=0):
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ExtractionError("OOXML structural integer is invalid") from exc
    if not 0 <= result <= 1_000_000:
        raise ExtractionError("OOXML structural integer exceeds its bound")
    return result


def _value(node, child, default=None):
    element = _child(node, child) if node is not None else None
    return _attr(element, "val", default) if element is not None else default


def _children(node):
    selected = None
    if _local_name(node.tag) == "AlternateContent":
        selected = _child(node, "Choice")
        if selected is None:
            selected = _child(node, "Fallback")
        if selected is None:
            return
    for index, child in enumerate(node):
        if selected is None or child is selected:
            yield index, child


class _Package:
    def __init__(self, archive):
        self.archive = archive
        self.names = set(archive.namelist())
        if len(self.names) != len(archive.namelist()):
            raise ExtractionError("OOXML package contains duplicate members")
        self.relationships = {}

    def xml(self, part):
        return _safe_archive_xml_root(self.archive, part)

    def rels(self, part):
        if part not in self.relationships:
            name = (
                posixpath.join(
                    posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
                )
                if part
                else "_rels/.rels"
            )
            result = {}
            if name in self.names:
                for node in self.xml(name):
                    if _local_name(node.tag) != "Relationship":
                        continue
                    target = node.get("Target", "")
                    resolved = posixpath.normpath(
                        target.lstrip("/")
                        if target.startswith("/")
                        else posixpath.join(posixpath.dirname(part), target)
                    )
                    external = (
                        node.get("TargetMode") == "External"
                        or ":" in target
                        or "\\" in target
                        or resolved.startswith("../")
                    )
                    result[node.get("Id")] = {
                        "type": node.get("Type", "").rsplit("/", 1)[-1],
                        "part": resolved
                        if not external and resolved in self.names
                        else None,
                    }
            self.relationships[part] = result
        return self.relationships[part]

    def related(self, part, identifier):
        return self.rels(part).get(identifier, {}).get("part")

    def main(self, fallback):
        return next(
            (
                r["part"]
                for r in self.rels("").values()
                if r["type"] == "officeDocument" and r["part"]
            ),
            fallback,
        )


class _Reader:
    def __init__(self, package):
        self.package = package
        self.units = []
        self.issues = Counter()
        self.characters = 0
        self.nodes = 0

    def emit(self, kind, location, text=""):
        text = normalize_text(text)
        self.characters += len(text)
        if self.characters > 50_000_000 or len(self.units) >= 200_000:
            raise ExtractionError("OOXML extraction exceeds its output budget")
        unit = UnitDraft(kind, location, text)
        self.units.extend(_bounded_unit(unit) if text else [unit])
        if len(self.units) > 200_000:
            raise ExtractionError("OOXML extraction exceeds its unit budget")

    def guard(self, depth):
        self.nodes += 1
        if depth > 64 or self.nodes > 2_000_000:
            raise ExtractionError("OOXML extraction exceeds its traversal budget")

    def image(self, node, location):
        parts = []
        for reference in node.iter():
            if _local_name(reference.tag) not in {"blip", "imagedata"}:
                continue
            identifier = _attr(reference, "embed") or _attr(reference, "id")
            part = self.package.related(location["part"], identifier)
            if part and part not in parts:
                parts.append(part)
        descriptions = []
        for prop in node.iter():
            if _local_name(prop.tag) in {"docPr", "cNvPr"}:
                for key in ("title", "descr"):
                    value = prop.get(key)
                    if value and value not in descriptions:
                        descriptions.append(value)
        self.emit(
            "embedded_object",
            {
                **location,
                "object_type": "image",
                "image_parts": parts,
                "source_crops": [
                    dict(n.attrib)
                    for n in _desc(node, "srcRect")
                    if any(v not in {"0", "0.0"} for v in n.attrib.values())
                ],
                "text_representation": "source_alternative_text",
            },
            "\n".join(descriptions),
        )
        self.issues["office_image_content_unread"] += 1

    def finish(self):
        issues = [
            {
                "code": code,
                "severity": "warning",
                "message": "A source-declared Office feature needs additional extraction.",
                "details": {"occurrences": count},
            }
            for code, count in sorted(self.issues.items())
        ]
        if not any(u.content for u in self.units):
            issues.append(
                {
                    "code": "no_extractable_text",
                    "severity": "warning",
                    "message": "No native text was found.",
                }
            )
        return ExtractionResult(self.units, issues)


class _WordReader(_Reader):
    def __init__(self, package, main):
        super().__init__(package)
        self.styles = {}
        self.numberings = {}
        self.references = {}
        for relation in package.rels(main).values():
            if relation["type"] == "styles" and relation["part"]:
                for style in _desc(package.xml(relation["part"]), "style"):
                    self.styles[_attr(style, "styleId")] = style
            elif relation["type"] == "numbering" and relation["part"]:
                self._numbering(package.xml(relation["part"]))

    def _numbering(self, root):
        abstract = {}
        for node in root:
            if _local_name(node.tag) == "abstractNum":
                abstract[_attr(node, "abstractNumId")] = {
                    _attr(level, "ilvl"): {
                        "marker_pattern": _value(level, "lvlText", ""),
                        "number_format": _value(level, "numFmt", ""),
                    }
                    for level in node
                    if _local_name(level.tag) == "lvl"
                }
        for node in root:
            if _local_name(node.tag) == "num":
                self.numberings[_attr(node, "numId")] = abstract.get(
                    _value(node, "abstractNumId"), {}
                )

    def paragraph_properties(self, node):
        direct = _child(node, "pPr")
        style_id = _value(direct, "pStyle")
        properties = [direct] if direct is not None else []
        current, seen = style_id, set()
        while current in self.styles and current not in seen and len(seen) < 32:
            seen.add(current)
            style = self.styles[current]
            prop = _child(style, "pPr")
            if prop is not None:
                properties.append(prop)
            current = _value(style, "basedOn")
        result = {"style": style_id} if style_id else {}
        outline = next(
            (
                _value(p, "outlineLvl")
                for p in properties
                if _value(p, "outlineLvl") is not None
            ),
            None,
        )
        if outline is not None and _integer(outline) < 9:
            result.update({"head_type": "outline", "level": _integer(outline) + 1})
        num_props = [_child(p, "numPr") for p in properties]
        num_id = next(
            (_value(p, "numId") for p in num_props if _value(p, "numId") is not None),
            None,
        )
        level = next(
            (_value(p, "ilvl") for p in num_props if _value(p, "ilvl") is not None), "0"
        )
        if num_id not in {None, "0"}:
            result.update({"numbering_ref": num_id, "list_level": _integer(level)})
            result.update(self.numberings.get(num_id, {}).get(level, {}))
            if result.get("number_format") == "bullet":
                result["marker_text"] = result.get("marker_pattern", "")
            else:
                self.issues["docx_list_marker_partial"] += 1
        return result

    def paragraph(self, node, location, depth):
        props = self.paragraph_properties(node)
        location = {**location, **props, "paragraph_element": location["element"]}
        kind = (
            "heading"
            if props.get("head_type") == "outline"
            else (
                "list_item"
                if "numbering_ref" in props
                else location.get("container_kind", "paragraph")
            )
        )
        if kind not in DOCX_UNITS:
            kind = "paragraph"
        chunks, segment = [], 0

        def flush():
            nonlocal segment
            if normalize_text("".join(chunks)):
                segment += 1
                self.emit(kind, {**location, "segment": segment}, "".join(chunks))
            chunks.clear()

        def inline(element, address, level):
            self.guard(level)
            name = _local_name(element.tag)
            if name == "t":
                chunks.append(element.text or "")
                return
            if name in {"tab", "br", "cr", "noBreakHyphen", "softHyphen"}:
                chunks.append(
                    {
                        "tab": "\t",
                        "br": "\n",
                        "cr": "\n",
                        "noBreakHyphen": "\u2011",
                        "softHyphen": "\u00ad",
                    }[name]
                )
                return
            if name in {"del", "moveFrom"}:
                self.issues["docx_tracked_changes_present"] += 1
                return
            if name in {
                "drawing",
                "pict",
                "object",
                "txbxContent",
                "oMath",
                "oMathPara",
            }:
                flush()
                self.walk(
                    element,
                    {
                        **location,
                        "element": address,
                        "owner_paragraph": location["paragraph_element"],
                    },
                    level,
                )
                return
            if name in {"instrText", "fldChar", "fldSimple"}:
                flush()
                instruction = (
                    (element.text or "")
                    if name == "instrText"
                    else _attr(element, "instr", "")
                )
                if len(instruction) > 16_384:
                    self.issues["docx_field_instruction_truncated"] += 1
                self.emit(
                    "field",
                    {
                        **location,
                        "element": address,
                        "field_type": name,
                        "instruction": instruction[:16_384],
                        "after_segment": segment,
                        "boundary": _attr(element, "fldCharType"),
                        "evaluation": "stored_result_only",
                    },
                )
                if name != "fldSimple":
                    return
            if name in {"footnoteReference", "endnoteReference", "commentReference"}:
                flush()
                identifier = _attr(element, "id")
                self.emit(
                    "embedded_object",
                    {
                        **location,
                        "element": address,
                        "reference_type": name,
                        "after_segment": segment,
                        "note": identifier,
                        "owner_paragraph": location["paragraph_element"],
                    },
                )
                return
            for index, child in _children(element):
                inline(child, f"{address}.{index}", level + 1)

        for index, child in enumerate(node):
            if _local_name(child.tag) != "pPr":
                inline(child, f"{location['element']}.{index}", depth + 1)
            else:
                for pi, prop in enumerate(child):
                    if _local_name(prop.tag) == "sectPr":
                        self.walk(
                            prop,
                            {
                                **location,
                                "element": f"{location['element']}.{index}.{pi}",
                            },
                            depth + 1,
                        )
        flush()

    def table(self, node, location, depth):
        parent = {k: location[k] for k in ("table", "cell") if k in location}
        context = {
            k: v
            for k, v in location.items()
            if k not in {"cell", "row", "col", "row_span", "col_span", "is_header"}
        }
        context.update({"table": location["element"], "container_kind": "table"})
        if parent:
            context["container_path"] = [*context.get("container_path", []), parent]
        rows = [(i, r) for i, r in enumerate(node) if _local_name(r.tag) == "tr"]
        grid = _child(node, "tblGrid")
        cols = len(grid) if grid is not None else 0
        self.emit("table", {**context, "rows": len(rows), "cols": cols})
        active, cells = {}, []
        for row_index, (ri, row) in enumerate(rows):
            row_props = _child(row, "trPr")
            col = _integer(_value(row_props, "gridBefore", "0"))
            next_active = {}
            for ci, cell in enumerate(row):
                if _local_name(cell.tag) != "tc":
                    continue
                prop = _child(cell, "tcPr")
                span = _integer(_value(prop, "gridSpan", "1"))
                if span < 1:
                    raise ExtractionError("OOXML table has a zero-width cell")
                vm = _child(prop, "vMerge") if prop is not None else None
                merge = _attr(vm, "val", "continue") if vm is not None else None
                loc = {
                    **context,
                    "element": f"{location['element']}.{ri}.{ci}",
                    "row": row_index,
                    "col": col,
                    "row_span": 1,
                    "col_span": span,
                    "is_header": _child(row_props, "tblHeader") is not None
                    if row_props is not None
                    else False,
                    "container_kind": "table_cell",
                }
                loc["cell"] = loc["element"]
                if (
                    merge == "continue"
                    and col in active
                    and active[col]["col_span"] == span
                ):
                    origin = active[col]
                    origin["row_span"] += 1
                    next_active[col] = origin
                    loc["merged_into"] = origin["cell"]
                elif merge == "continue":
                    self.issues["docx_table_merge_partial"] += 1
                elif merge == "restart":
                    next_active[col] = loc
                if prop is not None and _child(prop, "hMerge") is not None:
                    self.issues["docx_legacy_table_merge_partial"] += 1
                cells.append((cell, loc))
                col += span
            active = next_active
        for cell, loc in cells:
            if "merged_into" not in loc:
                self.emit("table_cell", {**loc, "structural_only": True})
            elif any((n.text or "").strip() for n in _desc(cell, "t")):
                self.issues["docx_table_merge_content_present"] += 1
            for index, child in enumerate(cell):
                if _local_name(child.tag) != "tcPr":
                    self.walk(
                        child,
                        {**loc, "element": f"{loc['element']}.{index}"},
                        depth + 1,
                    )

    def walk(self, node, location, depth=0):
        self.guard(depth)
        name = _local_name(node.tag)
        if name == "p":
            self.paragraph(node, location, depth)
            return
        if name == "tbl":
            self.table(node, location, depth)
            return
        if name in {"del", "moveFrom"}:
            self.issues["docx_tracked_changes_present"] += 1
            return
        if name in {"oMath", "oMathPara"}:
            self.emit(
                "embedded_object",
                {**location, "text_representation": "native_math_tokens"},
                " ".join(n.text or "" for n in _desc(node, "t")),
            )
            self.issues["docx_math_layout_partial"] += 1
            return
        if name in {"drawing", "pict", "object"}:
            location = {**location, "object": location["element"]}
            if any(_local_name(n.tag) in {"blip", "imagedata"} for n in node.iter()):
                self.image(node, location)
            if name == "object":
                self.issues["office_embedded_object_content_partial"] += 1
            self._textboxes(node, location, depth)
            if any(_local_name(n.tag) in {"chart", "relIds"} for n in node.iter()):
                self.issues["docx_drawing_content_partial"] += 1
            return
        if name == "txbxContent":
            location = {**location, "container_kind": "textbox"}
        if name in {"footnote", "endnote", "comment"}:
            identifier = _attr(node, "id", "")
            if identifier.startswith("-") or _attr(node, "type") in {
                "separator",
                "continuationSeparator",
            }:
                return
            location = {**location, "container_kind": name, "note": identifier}
        if name == "altChunk":
            self.issues["docx_imported_content_unread"] += 1
            return
        if name in {"headerReference", "footerReference"}:
            part = self.package.related(location["part"], _attr(node, "id"))
            if part:
                self.references.setdefault(part, []).append(location["element"])
        for index, child in _children(node):
            self.walk(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
            )

    def _textboxes(self, node, location, depth):
        self.guard(depth)
        if _local_name(node.tag) == "txbxContent":
            self.walk(node, location, depth + 1)
            return
        for index, child in _children(node):
            self._textboxes(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
            )


class _SlideReader(_Reader):
    def paragraphs(self, node, location, kind="slide_text"):
        for index, child in enumerate(node):
            if _local_name(child.tag) != "p":
                continue
            location_p = {**location, "element": f"{location['element']}.{index}"}
            prop = _child(child, "pPr")
            if prop is not None:
                location_p["list_level"] = _integer(prop.get("lvl", "0"))
                bullet, automatic = _child(prop, "buChar"), _child(prop, "buAutoNum")
                if bullet is not None:
                    location_p["marker_text"] = bullet.get("char", "")
                if automatic is not None:
                    location_p["numbering"] = dict(automatic.attrib)
                    self.issues["pptx_list_marker_partial"] += 1
            text = "".join(
                n.text or "" if _local_name(n.tag) == "t" else "\n"
                for n in child.iter()
                if _local_name(n.tag) in {"t", "br"}
            )
            if text.strip():
                self.emit(kind, location_p, text)

    def table(self, node, location):
        rows = [(i, r) for i, r in enumerate(node) if _local_name(r.tag) == "tr"]
        grid = _child(node, "tblGrid")
        context = {**location, "table": location["element"]}
        self.emit(
            "table",
            {
                **context,
                "rows": len(rows),
                "cols": len(grid) if grid is not None else 0,
            },
        )
        for row_index, (ri, row) in enumerate(rows):
            col = 0
            for ci, cell in enumerate(row):
                if _local_name(cell.tag) != "tc":
                    continue
                loc = {
                    **context,
                    "element": f"{location['element']}.{ri}.{ci}",
                    "row": row_index,
                    "col": col,
                    "row_span": _integer(cell.get("rowSpan", "1")),
                    "col_span": _integer(cell.get("gridSpan", "1")),
                }
                loc["cell"] = loc["element"]
                spanned = any(
                    cell.get(k) in {"1", "true"} for k in ("hMerge", "vMerge")
                )
                if not spanned:
                    self.emit("table_cell", {**loc, "structural_only": True})
                elif any((n.text or "").strip() for n in _desc(cell, "t")):
                    loc["merge_continuation"] = True
                    self.issues["pptx_table_merge_content_present"] += 1
                for ti, body in enumerate(cell):
                    if _local_name(body.tag) == "txBody":
                        self.paragraphs(
                            body,
                            {**loc, "element": f"{loc['element']}.{ti}"},
                            "table_cell",
                        )
                col += 1

    def related_text(self, node, location, kind):
        identifier = _attr(node, "id") if kind == "chart" else _attr(node, "dm")
        part = self.package.related(location["part"], identifier)
        if not part:
            self.issues[f"pptx_{kind}_content_unread"] += 1
            return
        root = self.package.xml(part)
        base = {
            **location,
            "owner_part": location["part"],
            "owner_element": location["element"],
            "part": part,
            "element": "0",
            "text_representation": "native_cached_data",
        }
        emitted_before = len(self.units)

        def walk(element, loc, depth=0):
            self.guard(depth)
            name = _local_name(element.tag)
            if kind == "diagram" and name == "pt":
                loc = {**loc, "diagram_point": element.get("modelId")}
            if kind == "chart" and name == "ser":
                index = _child(element, "idx")
                loc = {
                    **loc,
                    "series": index.get("val") if index is not None else loc["element"],
                }
            if name in {"cat", "val", "xVal", "yVal", "bubbleSize", "tx"}:
                loc = {**loc, "data_role": name}
            if kind == "chart" and name == "pt":
                value = _child(element, "v")
                if value is not None and value.text:
                    self.emit(
                        "chart_data",
                        {**loc, "point_index": element.get("idx")},
                        value.text,
                    )
                return
            if name in {"txBody", "rich"} or (kind == "diagram" and name == "t"):
                self.paragraphs(
                    element, loc, "diagram_text" if kind == "diagram" else "chart_data"
                )
                return
            if name == "v" and element.text:
                self.emit("chart_data", loc, element.text)
                return
            for index, child in enumerate(element):
                walk(child, {**loc, "element": f"{loc['element']}.{index}"}, depth + 1)

        walk(root, base)
        if len(self.units) == emitted_before:
            self.issues[f"pptx_{kind}_content_unread"] += 1

    def walk(self, node, location, depth=0, notes=False):
        self.guard(depth)
        name = _local_name(node.tag)
        if name in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}:
            nonvisual = next(
                (c for c in node if _local_name(c.tag).startswith("nv")), None
            )
            prop = _child(nonvisual, "cNvPr") if nonvisual is not None else None
            if prop is not None:
                location = {
                    **location,
                    "shape_id": prop.get("id"),
                    "shape_name": prop.get("name", ""),
                    "object": location["element"],
                }
            transform_parent = _child(node, "grpSpPr" if name == "grpSp" else "spPr")
            transform = (
                _child(transform_parent, "xfrm")
                if transform_parent is not None
                else _child(node, "xfrm")
            )
            if transform is not None:
                location = {
                    **location,
                    "source_transform": {
                        "attributes": dict(transform.attrib),
                        **{_local_name(c.tag): dict(c.attrib) for c in transform},
                        "coordinate_space": "parent_group_emu",
                    },
                }
            if name == "grpSp":
                location = {
                    **location,
                    "group_path": [
                        *location.get("group_path", []),
                        location["element"],
                    ],
                }
            if notes and name == "sp":
                placeholder = next(_desc(node, "ph"), None)
                if placeholder is not None and placeholder.get("type") in {
                    "sldImg",
                    "sldNum",
                    "dt",
                    "hdr",
                    "ftr",
                }:
                    return
            if name == "pic":
                self.image(node, location)
                return
        if name == "txBody":
            self.paragraphs(node, location, "speaker_notes" if notes else "slide_text")
            return
        if name == "tbl":
            self.table(node, location)
            return
        if name in {"chart", "relIds"}:
            self.related_text(node, location, "chart" if name == "chart" else "diagram")
            return
        if name in {"oleObj", "videoFile", "audioFile"}:
            self.emit("embedded_object", {**location, "object_type": name})
            self.issues["office_embedded_object_content_partial"] += 1
            return
        for index, child in _children(node):
            self.walk(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
                notes,
            )


def extract_structured_docx(path):
    _preflight_zip(path)
    with zipfile.ZipFile(path) as archive:
        package = _Package(archive)
        main = package.main("word/document.xml")
        reader = _WordReader(package, main)
        reader.walk(package.xml(main), {"part": main, "element": "0"})
        seen = {main}
        for relation in package.rels(main).values():
            part, kind = relation["part"], relation["type"]
            if (
                not part
                or part in seen
                or kind not in {"header", "footer", "footnotes", "endnotes", "comments"}
                or (kind in {"header", "footer"} and part not in reader.references)
            ):
                continue
            seen.add(part)
            reader.walk(
                package.xml(part),
                {
                    "part": part,
                    "element": "0",
                    "container_kind": kind,
                    "section_references": reader.references.get(part, []),
                },
            )
        return reader.finish()


def extract_structured_pptx(path):
    _preflight_zip(path)
    with zipfile.ZipFile(path) as archive:
        package = _Package(archive)
        main = package.main("ppt/presentation.xml")
        reader = _SlideReader(package)
        slide_list = _child(package.xml(main), "sldIdLst")
        for slide_index, slide in enumerate(
            slide_list if slide_list is not None else [], 1
        ):
            identifier = next(
                (v for k, v in slide.attrib.items() if k.endswith("}id")), None
            )
            part = package.related(main, identifier)
            if not part:
                reader.issues["pptx_slide_unavailable"] += 1
                continue
            reader.walk(
                package.xml(part),
                {
                    "slide": slide_index,
                    "slide_id": slide.get("id"),
                    "part": part,
                    "element": "0",
                },
            )
            for relation in package.rels(part).values():
                if relation["type"] == "notesSlide" and relation["part"]:
                    reader.walk(
                        package.xml(relation["part"]),
                        {
                            "slide": slide_index,
                            "part": relation["part"],
                            "element": "0",
                        },
                        notes=True,
                    )
        return reader.finish()
