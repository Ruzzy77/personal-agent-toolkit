#!/usr/bin/env python3
"""Build the Library Brush display font from raster glyph sources."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fontTools.agl import UV2AGL
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


UNITS_PER_EM = 1000
CAP_HEIGHT = 760
ASCENT = 840
DESCENT = -260
RASTER_HEIGHT = 160
SIDE_BEARING = 38


def clean_small_components(mask: np.ndarray, minimum_area: int = 4) -> np.ndarray:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    cleaned = mask.copy()

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            component = []
            while stack:
                cy, cx = stack.pop()
                component.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(component) < minimum_area:
                for cy, cx in component:
                    cleaned[cy, cx] = False

    return cleaned


def largest_connected_component(mask: np.ndarray) -> np.ndarray:
    """Keep one complete letter when a wordmark crop also contains its neighbor."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    largest: list[tuple[int, int]] = []

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            component = []
            while stack:
                cy, cx = stack.pop()
                component.append((cy, cx))
                for ny in range(max(0, cy - 1), min(height, cy + 2)):
                    for nx in range(max(0, cx - 1), min(width, cx + 2)):
                        if mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            if len(component) > len(largest):
                largest = component

    if not largest:
        raise ValueError("Glyph source contains no visible ink")
    result = np.zeros_like(mask, dtype=bool)
    for y, x in largest:
        result[y, x] = True
    return result


def connected_component_areas(mask: np.ndarray) -> list[int]:
    """Measure visible pieces after raster cleanup."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    areas = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            area = 0
            while stack:
                cy, cx = stack.pop()
                area += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            areas.append(area)
    return sorted(areas, reverse=True)


def adjust_stroke_weight(mask: np.ndarray, amount: int) -> np.ndarray:
    """Apply a small per-glyph weight correction without scaling its proportions."""
    if amount == 0:
        return mask
    result = np.pad(mask, abs(amount) + 1, constant_values=False)
    for _ in range(abs(amount)):
        up = np.zeros_like(result)
        down = np.zeros_like(result)
        left = np.zeros_like(result)
        right = np.zeros_like(result)
        up[:-1] = result[1:]
        down[1:] = result[:-1]
        left[:, :-1] = result[:, 1:]
        right[:, 1:] = result[:, :-1]
        if amount > 0:
            result = result | up | down | left | right
        else:
            result = result & up & down & left & right
    rows, columns = np.where(result)
    if not len(rows):
        raise ValueError("Stroke-weight correction erased the glyph")
    return clean_small_components(result[rows.min():rows.max() + 1, columns.min():columns.max() + 1])


def source_ink_mask(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = pixels[:, :, 3]
    if alpha.min() < 250:
        return alpha >= 48

    # Some generated sheets contain a baked transparency checkerboard.
    # Its neutral light cells are excluded by this luminance threshold.
    red = pixels[:, :, 0].astype(np.uint16)
    green = pixels[:, :, 1].astype(np.uint16)
    blue = pixels[:, :, 2].astype(np.uint16)
    luminance = (red * 54 + green * 183 + blue * 19) // 256
    return luminance < 150


def glyph_mask(
    image: Image.Image,
    width_scale: float = 1.0,
    height_scale: float = 1.0,
    largest_component_only: bool = False,
) -> np.ndarray:
    source = source_ink_mask(image)
    if largest_component_only:
        source = largest_connected_component(source)
    rows, columns = np.where(source)
    if not len(rows):
        raise ValueError("Glyph source contains no visible ink")
    source = source[rows.min():rows.max() + 1, columns.min():columns.max() + 1]
    target_height = max(1, round(RASTER_HEIGHT * height_scale))
    target_width = max(1, round(source.shape[1] * (target_height / source.shape[0]) * width_scale))
    source_image = Image.fromarray(source.astype(np.uint8) * 255, mode="L")
    resized = source_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    mask = np.asarray(resized, dtype=np.uint8) >= 96
    return clean_small_components(mask)


def boundary_edges(mask: np.ndarray) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    """Return clockwise exterior and counter-clockwise counter edges."""
    height, width = mask.shape
    edges = set()
    for row in range(height):
        for x in range(width):
            if not mask[row, x]:
                continue
            top = height - row
            bottom = top - 1
            if row == 0 or not mask[row - 1, x]:
                edges.add(((x, top), (x + 1, top)))
            if x == width - 1 or not mask[row, x + 1]:
                edges.add(((x + 1, top), (x + 1, bottom)))
            if row == height - 1 or not mask[row + 1, x]:
                edges.add(((x + 1, bottom), (x, bottom)))
            if x == 0 or not mask[row, x - 1]:
                edges.add(((x, bottom), (x, top)))
    return edges


def edge_direction(edge: tuple[tuple[int, int], tuple[int, int]]) -> int:
    (x0, y0), (x1, y1) = edge
    delta = (x1 - x0, y1 - y0)
    return {(1, 0): 0, (0, -1): 1, (-1, 0): 2, (0, 1): 3}[delta]


def trace_contours(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    remaining = boundary_edges(mask)
    contours = []

    while remaining:
        first = next(iter(remaining))
        remaining.remove(first)
        start = first[0]
        current = first
        points = [start, first[1]]

        while points[-1] != start:
            candidates = [edge for edge in remaining if edge[0] == points[-1]]
            if not candidates:
                raise ValueError("Open raster contour")
            incoming = edge_direction(current)
            priority = [(incoming + 1) % 4, incoming, (incoming - 1) % 4, (incoming + 2) % 4]
            candidates.sort(key=lambda edge: priority.index(edge_direction(edge)))
            current = candidates[0]
            remaining.remove(current)
            points.append(current[1])

        # Keep only corners; straight grid segments become a single font segment.
        simplified = []
        for index in range(len(points) - 1):
            previous = points[index - 1]
            point = points[index]
            following = points[(index + 1) % (len(points) - 1)]
            before = (point[0] - previous[0], point[1] - previous[1])
            after = (following[0] - point[0], following[1] - point[1])
            if before != after:
                simplified.append(point)
        if len(simplified) >= 3:
            contours.append(simplified)

    return contours


def mask_to_glyph(mask: np.ndarray, bottom_scale: float = 0.0):
    pen = TTGlyphPen(None)
    pixel = CAP_HEIGHT / RASTER_HEIGHT
    bottom = RASTER_HEIGHT * bottom_scale

    for contour in trace_contours(mask):
        points = [(round(SIDE_BEARING + x * pixel), round((y + bottom) * pixel)) for x, y in contour]
        pen.moveTo(points[0])
        for point in points[1:]:
            pen.lineTo(point)
        pen.closePath()

    advance = round(mask.shape[1] * pixel + SIDE_BEARING * 2)
    return pen.glyph(), (advance, SIDE_BEARING)


def empty_glyph():
    return TTGlyphPen(None).glyph()


def glyph_name(character: str) -> str:
    return UV2AGL.get(ord(character), f"uni{ord(character):04X}")


def build_font(
    wordmark_path: Path,
    sheets: list[tuple[Path, str]],
    override_sheets: list[tuple[Path, str]],
    output_ttf: Path,
    output_woff2: Path,
) -> None:
    wordmark = Image.open(wordmark_path).convert("RGBA")

    # Wide crops retain each complete wordmark letter. Selecting the largest
    # connected component removes neighboring strokes from overlapping crops.
    base_crops = {
        "L": (5, 20, 94, 190),
        "I": (90, 20, 137, 190),
        "B": (135, 20, 232, 190),
        "R": (230, 20, 324, 190),
        "A": (312, 20, 422, 190),
        "Y": (495, 20, 598, 190),
    }

    masks: dict[str, np.ndarray] = {}
    for character, box in base_crops.items():
        masks[character] = glyph_mask(wordmark.crop(box), largest_component_only=True)

    height_scales = {
        ".": 0.16,
        ":": 0.46,
        "·": 0.16,
        "-": 0.14,
        "+": 0.50,
        "'": 0.18,
        **{character: 1.02 for character in "COQ"},
        **{character: 0.70 for character in "mnrsuvwxz"},
        **{character: 0.72 for character in "aceo"},
        **{character: 0.95 for character in "bdhkl"},
        "f": 0.92,
        "i": 0.90,
        "t": 0.82,
        **{character: 0.92 for character in "gpqy"},
        "j": 1.15,
    }
    bottom_scales = {
        ".": 0.0,
        ":": 0.25,
        "·": 0.42,
        "-": 0.43,
        "+": 0.25,
        "'": 0.72,
        **{character: -0.01 for character in "COQaceo"},
        **{character: -0.22 for character in "gpqy"},
        "j": -0.27,
    }

    def read_sheet(sheet_path: Path, characters: str) -> dict[str, tuple[np.ndarray, int]]:
        if len(characters) == 9:
            columns, rows = 3, 3
        elif len(characters) == 3:
            columns, rows = 3, 1
        elif len(characters) == 1:
            columns, rows = 1, 1
        else:
            raise ValueError(f"Sheet must describe exactly one, three, or nine glyphs: {sheet_path}")
        sheet = Image.open(sheet_path).convert("RGBA")
        cell_width = sheet.width // columns
        cell_height = sheet.height // rows
        result = {}
        for index, character in enumerate(characters):
            row, column = divmod(index, columns)
            box = (
                column * cell_width,
                row * cell_height,
                sheet.width if column == columns - 1 else (column + 1) * cell_width,
                sheet.height if row == rows - 1 else (row + 1) * cell_height,
            )
            cell = sheet.crop(box)
            cell_ink = source_ink_mask(cell)
            ink_rows, ink_columns = np.where(cell_ink)
            if not len(ink_rows):
                raise ValueError(f"Glyph source contains no visible ink: {sheet_path} [{character}]")
            clearance = min(
                int(ink_columns.min()),
                int(ink_rows.min()),
                int(cell.width - ink_columns.max() - 1),
                int(cell.height - ink_rows.max() - 1),
            )
            result[character] = (
                glyph_mask(
                    cell,
                    width_scale=0.9,
                    height_scale=height_scales.get(character, 1.0),
                ),
                clearance,
            )
        return result

    source_clearance = {}
    for sheet_path, characters in sheets:
        for character, (mask, clearance) in read_sheet(sheet_path, characters).items():
            if character not in masks:
                masks[character] = mask
                source_clearance[character] = clearance

    for sheet_path, characters in override_sheets:
        for character, (mask, clearance) in read_sheet(sheet_path, characters).items():
            masks[character] = mask
            source_clearance[character] = clearance

    # Per-glyph optical weight corrections, measured against peers at cap height
    # and x-height. Positive values expand the stroke by raster pixels; negative
    # values contract it while preserving the original letter proportions.
    stroke_weight_adjustments = {
        **{character: 3 for character in "AIR"},
        "C": 2,
        **{character: 2 for character in "BEFGX"},
        **{character: 1 for character in "DHK"},
        "L": 3,
        "Y": 6,
        **{character: -1 for character in "TZ"},
        **{character: -2 for character in "JMN P Q S U".replace(" ", "")},
        "O": -4,
        **{character: -4 for character in "VW"},
        "f": 1,
        "g": 1,
        "i": 3,
        "k": 1,
        "x": 3,
        **{character: -1 for character in "hjlnopq"},
        "m": -3,
        "1": 2,
        "9": 1,
        "0": -1,
        "6": -1,
    }
    masks = {
        character: adjust_stroke_weight(mask, stroke_weight_adjustments.get(character, 0))
        for character, mask in masks.items()
    }

    clipped = {character: value for character, value in source_clearance.items() if value <= 2}
    if clipped:
        details = ", ".join(f"{character}={value}px" for character, value in sorted(clipped.items()))
        raise ValueError(f"Glyph source touches a cell boundary: {details}")

    expected = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789&'.:·-/+!?")
    missing = expected - set(masks)
    if missing:
        raise ValueError(f"Missing glyphs: {''.join(sorted(missing))}")

    expected_component_counts = {"i": 2, "j": 2, ":": 2, "!": 2, "?": 2}
    unexpected_components = {}
    for character, mask in masks.items():
        count = len(connected_component_areas(mask))
        expected_count = expected_component_counts.get(character, 1)
        if count != expected_count:
            unexpected_components[character] = (count, expected_count)
    if unexpected_components:
        details = ", ".join(
            f"{character}={count} (expected {expected_count})"
            for character, (count, expected_count) in sorted(unexpected_components.items())
        )
        raise ValueError(f"Glyph contains an unexpected detached component: {details}")

    characters = sorted(masks, key=ord)
    names = {character: glyph_name(character) for character in characters}
    glyph_order = [".notdef", "space", *[names[character] for character in characters]]
    glyphs = {".notdef": empty_glyph(), "space": empty_glyph()}
    metrics = {".notdef": (600, 40), "space": (320, 0)}

    for character in characters:
        name = names[character]
        glyphs[name], metrics[name] = mask_to_glyph(masks[character], bottom_scales.get(character, 0.0))

    character_map = {ord(character): names[character] for character in characters}
    for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        lowercase = character.lower()
        if lowercase not in names:
            character_map[ord(lowercase)] = names[character]

    builder = FontBuilder(UNITS_PER_EM, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(character_map)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)
    builder.setupNameTable({
        "familyName": "Library Brush",
        "styleName": "Regular",
        "uniqueFontIdentifier": "Library Brush 2.2",
        "fullName": "Library Brush Regular",
        "psName": "LibraryBrush-Regular",
        "version": "Version 2.2",
    })
    builder.setupOS2(
        sTypoAscender=ASCENT,
        sTypoDescender=DESCENT,
        sTypoLineGap=0,
        usWinAscent=ASCENT,
        usWinDescent=abs(DESCENT),
        usWeightClass=700,
        usWidthClass=4,
        sxHeight=round(CAP_HEIGHT * 0.70),
        sCapHeight=CAP_HEIGHT,
    )
    builder.setupPost()
    builder.setupMaxp()
    # Keep rebuilt binaries byte-for-byte reproducible instead of embedding the
    # current time in the OpenType head table.
    builder.font["head"].created = 2082844800
    builder.font["head"].modified = 2082844800

    output_ttf.parent.mkdir(parents=True, exist_ok=True)
    builder.font.save(output_ttf)
    builder.font.flavor = "woff2"
    builder.font.save(output_woff2)


def render_specimen(font_path: Path, output_path: Path) -> None:
    background = "#081747"
    ink = "#c9ed12"
    canvas = Image.new("RGB", (1600, 1120), background)
    draw = ImageDraw.Draw(canvas)
    font_large = ImageFont.truetype(str(font_path), 206)
    font_medium = ImageFont.truetype(str(font_path), 94)
    font_small = ImageFont.truetype(str(font_path), 58)
    draw.text((70, 24), "Library Brush", font=font_large, fill=ink)
    draw.text((72, 280), "Research Digest · 13", font=font_medium, fill=ink)
    draw.text((72, 440), "Daily / Research / 25 Aug 2026", font=font_medium, fill=ink)
    draw.text((72, 640), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", font=font_small, fill=ink)
    draw.text((72, 755), "abcdefghijklmnopqrstuvwxyz", font=font_small, fill=ink)
    draw.text((72, 895), "0123456789 · : / + ! ? & - ' ", font=font_medium, fill=ink)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wordmark", type=Path, required=True)
    parser.add_argument(
        "--sheet",
        action="append",
        nargs=2,
        metavar=("PATH", "GLYPHS"),
        required=True,
        help="One-glyph, three-across, or three-by-three sheet and its glyphs in reading order",
    )
    parser.add_argument(
        "--override-sheet",
        action="append",
        nargs=2,
        metavar=("PATH", "GLYPHS"),
        default=[],
        help="One-glyph, three-across, or three-by-three sheet that replaces glyphs loaded earlier",
    )
    parser.add_argument("--ttf", type=Path, required=True)
    parser.add_argument("--woff2", type=Path, required=True)
    parser.add_argument("--specimen", type=Path, required=True)
    args = parser.parse_args()

    build_font(
        args.wordmark,
        [(Path(path), characters) for path, characters in args.sheet],
        [(Path(path), characters) for path, characters in args.override_sheet],
        args.ttf,
        args.woff2,
    )
    render_specimen(args.ttf, args.specimen)


if __name__ == "__main__":
    main()
