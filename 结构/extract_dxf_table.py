import os
import sys
import csv
import json
import time
from typing import Any, Dict, List, Tuple, Optional

import ezdxf
from ezdxf.addons import Importer

# Input/Output configuration (direct assignment per user preference)
BASE_DIR = os.path.dirname(__file__)
INPUT_DXF = os.path.join(BASE_DIR, "桩位图.dxf")
TEXT_CSV = os.path.join(BASE_DIR, "dxf_texts.csv")
BLOCK_CSV = os.path.join(BASE_DIR, "dxf_blocks.csv")

MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2
GRID_SNAP_TOL = 0.5  # strict snapping so close lines are not merged


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def vec3_to_xy(vec: Any) -> Tuple[float, float]:
    try:
        return safe_float(vec[0]), safe_float(vec[1])
    except Exception:
        try:
            return safe_float(vec.x), safe_float(vec.y)
        except Exception:
            return 0.0, 0.0


def read_texts(doc: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for layout in doc.layouts:
        space = layout
        layout_name = layout.name

        # TEXT entities
        for e in space.query("TEXT"):
            x, y = vec3_to_xy(e.dxf.insert)
            rows.append(
                {
                    "layout": layout_name,
                    "entity": "TEXT",
                    "layer": e.dxf.layer,
                    "text": e.dxf.text or "",
                    "x": x,
                    "y": y,
                    "height": safe_float(getattr(e.dxf, "height", 0)),
                    "rotation": safe_float(getattr(e.dxf, "rotation", 0)),
                    "table_name": "",
                    "row": "",
                    "col": "",
                }
            )

        # MTEXT entities
        for e in space.query("MTEXT"):
            x, y = vec3_to_xy(e.dxf.insert)
            try:
                content = e.plain_text()
            except Exception:
                content = getattr(e, "text", "") or getattr(e.dxf, "text", "")
            rows.append(
                {
                    "layout": layout_name,
                    "entity": "MTEXT",
                    "layer": e.dxf.layer,
                    "text": content,
                    "x": x,
                    "y": y,
                    "height": safe_float(getattr(e.dxf, "char_height", getattr(e.dxf, "height", 0))),
                    "rotation": safe_float(getattr(e.dxf, "rotation", 0)),
                    "table_name": "",
                    "row": "",
                    "col": "",
                }
            )

        # TABLE cells (best-effort; depends on DXF content)
        try:
            for t in space.query("TABLE"):
                tname = getattr(t.dxf, "name", "TABLE")
                nrows = int(getattr(t, "nrows", 0) or 0)
                ncols = int(getattr(t, "ncols", 0) or 0)
                if nrows < MIN_TABLE_ROWS or ncols < MIN_TABLE_COLS:
                    continue
                for r in range(nrows):
                    for c in range(ncols):
                        try:
                            cell = t.get_cell(r, c) if hasattr(t, "get_cell") else t.cell(r, c)
                        except Exception:
                            cell = None
                        if cell is None:
                            continue
                        value = ""
                        try:
                            if hasattr(cell, "plain_text"):
                                value = cell.plain_text()
                            elif hasattr(cell, "text"):
                                value = cell.text
                            elif hasattr(cell, "value"):
                                value = str(cell.value)
                            # Best-effort: detect block content on cell
                            if (not value) and hasattr(cell, "content"):
                                content = getattr(cell, "content")
                                # content can be list-like or single object
                                items = content if isinstance(content, (list, tuple)) else [content]
                                block_texts: List[str] = []
                                for it in items:
                                    try:
                                        # common attributes for block content
                                        bname = getattr(it, "name", getattr(it, "block_name", getattr(it, "block", "")))
                                        if not bname and hasattr(it, "dxf"):
                                            bname = getattr(it.dxf, "name", "")
                                        if bname:
                                            block_texts.append(f"{bname}")
                                    except Exception:
                                        continue
                                if block_texts:
                                    value = ", ".join(block_texts)
                        except Exception:
                            value = ""
                        if value:
                            rows.append(
                                {
                                    "layout": layout_name,
                                    "entity": "TABLE",
                                    "layer": t.dxf.layer,
                                    "text": value,
                                    "x": "",
                                    "y": "",
                                    "height": "",
                                    "rotation": "",
                                    "table_name": tname,
                                    "row": r,
                                    "col": c,
                                }
                            )
        except Exception:
            # TABLE may not exist or be unsupported; ignore gracefully
            pass

    return rows


def read_blocks(doc: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for layout in doc.layouts:
        space = layout
        layout_name = layout.name
        for e in space.query("INSERT"):
            x, y = vec3_to_xy(e.dxf.insert)
            name = e.dxf.name

            # Extract attributes into a JSON string
            attrs: Dict[str, Any] = {}
            try:
                for a in e.attribs():
                    tag = getattr(a.dxf, "tag", "")
                    text = getattr(a, "text", getattr(a.dxf, "text", ""))
                    if tag:
                        attrs[tag] = text
            except Exception:
                pass

            is_legend = 1 if ("图例" in (name or "") or "图例" in (e.dxf.layer or "")) else 0

            rows.append(
                {
                    "layout": layout_name,
                    "layer": e.dxf.layer,
                    "block_name": name,
                    "x": x,
                    "y": y,
                    "rotation": safe_float(getattr(e.dxf, "rotation", 0)),
                    "xscale": safe_float(getattr(e.dxf, "xscale", 1)),
                    "yscale": safe_float(getattr(e.dxf, "yscale", 1)),
                    "is_legend": is_legend,
                    "attributes_json": json.dumps(attrs, ensure_ascii=False),
                }
            )
    return rows


def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    # Use UTF-8 with BOM for better Excel compatibility with Chinese text
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def sanitize_filename(name: str) -> str:
    bad = "\\/:*?\"<>|"
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip() or "TABLE"


def cell_to_text(cell: Any) -> str:
    # Prefer plain text
    try:
        if hasattr(cell, "plain_text"):
            txt = cell.plain_text()
            if txt:
                return txt
    except Exception:
        pass
    try:
        for attr in ("text", "value"):
            if hasattr(cell, attr):
                val = getattr(cell, attr)
                if val:
                    return str(val)
    except Exception:
        pass
    # Try to extract embedded block name(s)
    try:
        if hasattr(cell, "content"):
            content = getattr(cell, "content")
            items = content if isinstance(content, (list, tuple)) else [content]
            names: List[str] = []
            for it in items:
                try:
                    bname = getattr(it, "name", getattr(it, "block_name", getattr(it, "block", "")))
                    if not bname and hasattr(it, "dxf"):
                        bname = getattr(it.dxf, "name", "")
                    if bname:
                        names.append(bname)
                except Exception:
                    continue
            if names:
                return ", ".join(names)
    except Exception:
        pass
    return ""


def _forward_fill_grid(grid: List[List[str]]) -> List[List[str]]:
    # Fill horizontally (left to right)
    for r in range(len(grid)):
        last = ""
        for c in range(len(grid[r])):
            if grid[r][c] == "" and last != "":
                grid[r][c] = last
            else:
                last = grid[r][c]
    # Fill vertically (top to bottom)
    for c in range(len(grid[0]) if grid else 0):
        last = ""
        for r in range(len(grid)):
            if grid[r][c] == "" and last != "":
                grid[r][c] = last
            else:
                last = grid[r][c]
    return grid


# ------------------ Grid-based table fallback ------------------

def _axis_aligned_segments(space: Any, tol: float = 1e-6, layer_filter: Optional[str] = None) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
    # Returns (vertical_segments, horizontal_segments)
    v_segments: List[Tuple[float, float, float]] = []  # (x, y1, y2)
    h_segments: List[Tuple[float, float, float]] = []  # (y, x1, x2)

    def add_seg(x1: float, y1: float, x2: float, y2: float) -> None:
        if abs(x1 - x2) <= tol and abs(y1 - y2) > tol:
            ylo, yhi = sorted([y1, y2])
            v_segments.append((x1, ylo, yhi))
        elif abs(y1 - y2) <= tol and abs(x1 - x2) > tol:
            xlo, xhi = sorted([x1, x2])
            h_segments.append((y1, xlo, xhi))

    # LINE entities (filter by layer if provided)
    try:
        for e in space.query("LINE"):
            if layer_filter and str(getattr(e.dxf, "layer", "")).strip().upper() != layer_filter.upper():
                continue
            x1, y1 = vec3_to_xy(e.dxf.start)
            x2, y2 = vec3_to_xy(e.dxf.end)
            add_seg(x1, y1, x2, y2)
    except Exception:
        pass

    # LWPOLYLINE entities (filter by layer if provided)
    try:
        for e in space.query("LWPOLYLINE"):
            if layer_filter and str(getattr(e.dxf, "layer", "")).strip().upper() != layer_filter.upper():
                continue
            pts = list(e.get_points("xy")) if hasattr(e, "get_points") else list(getattr(e, "points", []))
            if len(pts) < 2:
                continue
            closed = bool(getattr(e, "closed", getattr(e.dxf, "flags", 0) & 1))
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                add_seg(x1, y1, x2, y2)
            if closed:
                x1, y1 = pts[-1]
                x2, y2 = pts[0]
                add_seg(x1, y1, x2, y2)
    except Exception:
        pass

    return v_segments, h_segments


def _snap_positions(values: List[float], tol: float) -> List[float]:
    if not values:
        return []
    values = sorted(values)
    groups: List[List[float]] = []
    current: List[float] = [values[0]]
    for v in values[1:]:
        if abs(v - current[-1]) <= tol:
            current.append(v)
        else:
            groups.append(current)
            current = [v]
    groups.append(current)
    return [sum(g) / len(g) for g in groups]


def _grid_from_segments(v_segments: List[Tuple[float, float, float]], h_segments: List[Tuple[float, float, float]], tol: float = GRID_SNAP_TOL) -> Tuple[List[float], List[float]]:
    # Snap x and y positions and keep those that appear frequently
    xs = [x for (x, y1, y2) in v_segments]
    ys = [y for (y, x1, x2) in h_segments]
    xs = _snap_positions(xs, tol)
    ys = _snap_positions(ys, tol)
    # Must have at least 2 lines in each direction
    if len(xs) < 3 or len(ys) < 3:
        return [], []
    return xs, ys


# Row-specific vertical splits: collect vertical lines that cross the row band [y_bottom, y_top]
# and snap x positions to avoid mixing columns across different rows.
def _row_vertical_splits(v_segments: List[Tuple[float, float, float]], y_bottom: float, y_top: float, tol: float = GRID_SNAP_TOL) -> List[float]:
    xs: List[float] = []
    y_lo = min(y_bottom, y_top) - tol
    y_hi = max(y_bottom, y_top) + tol
    for (x, y1, y2) in v_segments:
        vy_lo = min(y1, y2)
        vy_hi = max(y1, y2)
        # vertical segment intersects the row band
        if not (vy_hi < y_lo or vy_lo > y_hi):
            xs.append(x)
    return sorted(_snap_positions(xs, tol))


def _bbox_from_grid(xs: List[float], ys: List[float]) -> Tuple[float, float, float, float]:
    return min(xs), min(ys), max(xs), max(ys)


def _point_in_rect(x: float, y: float, rect: Tuple[float, float, float, float], tol: float = 1e-6) -> bool:
    x1, y1, x2, y2 = rect
    return (x >= min(x1, x2) - tol) and (x <= max(x1, x2) + tol) and (y >= min(y1, y2) - tol) and (y <= max(y1, y2) + tol)


def _collect_cell_content(space: Any, rect: Tuple[float, float, float, float]) -> str:
    texts: List[str] = []
    x1, y1, x2, y2 = rect

    # TEXT
    try:
        for e in space.query("TEXT"):
            x, y = vec3_to_xy(e.dxf.insert)
            if _point_in_rect(x, y, rect):
                if e.dxf.text:
                    texts.append(str(e.dxf.text))
    except Exception:
        pass

    # MTEXT
    try:
        for e in space.query("MTEXT"):
            x, y = vec3_to_xy(e.dxf.insert)
            if _point_in_rect(x, y, rect):
                try:
                    content = e.plain_text()
                except Exception:
                    content = getattr(e, "text", "") or getattr(e.dxf, "text", "")
                if content:
                    texts.append(str(content))
    except Exception:
        pass

    # INSERT (block) -> convert to text (names only)
    try:
        for e in space.query("INSERT"):
            x, y = vec3_to_xy(e.dxf.insert)
            if _point_in_rect(x, y, rect):
                name = getattr(e.dxf, "name", "")
                if name:
                    texts.append(str(name))
    except Exception:
        pass

    # Merge texts; prefer joining with \n to preserve multiple items
    merged = "\n".join([t for t in (s.strip() for s in texts) if t])
    return merged

# ---- clustering to isolate inner table ----

def _segments_intersect(v: Tuple[float, float, float], h: Tuple[float, float, float], tol: float = 2.0) -> bool:
    x, y1, y2 = v
    y, x1, x2 = h
    if x < min(x1, x2) - tol or x > max(x1, x2) + tol:
        return False
    if y < min(y1, y2) - tol or y > max(y1, y2) + tol:
        return False
    return True


def _cluster_segments(v_segments: List[Tuple[float, float, float]], h_segments: List[Tuple[float, float, float]], tol: float = 2.0) -> List[Tuple[List[int], List[int]]]:
    clusters: List[Tuple[List[int], List[int]]] = []
    if not v_segments or not h_segments:
        return clusters
    nV = len(v_segments)
    nH = len(h_segments)
    # Build bipartite adjacency
    v_to_h: List[List[int]] = [[] for _ in range(nV)]
    h_to_v: List[List[int]] = [[] for _ in range(nH)]
    for i, v in enumerate(v_segments):
        vx, vy1, vy2 = v
        for j, h in enumerate(h_segments):
            if _segments_intersect(v, h, tol=tol):
                v_to_h[i].append(j)
                h_to_v[j].append(i)
    visited_v = [False] * nV
    visited_h = [False] * nH
    from collections import deque
    for i in range(nV):
        if visited_v[i]:
            continue
        # start a component from v i if it has any connections
        if not v_to_h[i]:
            continue
        comp_v: List[int] = []
        comp_h: List[int] = []
        dq: deque = deque()
        dq.append(('v', i))
        visited_v[i] = True
        while dq:
            kind, idx = dq.popleft()
            if kind == 'v':
                comp_v.append(idx)
                for hj in v_to_h[idx]:
                    if not visited_h[hj]:
                        visited_h[hj] = True
                        dq.append(('h', hj))
            else:
                comp_h.append(idx)
                for vi in h_to_v[idx]:
                    if not visited_v[vi]:
                        visited_v[vi] = True
                        dq.append(('v', vi))
        if comp_v and comp_h:
            clusters.append((comp_v, comp_h))
    return clusters


def _choose_best_cluster(v_segments: List[Tuple[float, float, float]], h_segments: List[Tuple[float, float, float]]) -> Tuple[List[float], List[float]]:
    clusters = _cluster_segments(v_segments, h_segments)
    best: Optional[Tuple[List[float], List[float], float]] = None
    for comp_v, comp_h in clusters:
        vs = [v_segments[i] for i in comp_v]
        hs = [h_segments[j] for j in comp_h]
        xs, ys = _grid_from_segments(vs, hs)
        if len(xs) < 3 or len(ys) < 3:
            continue
        x1, y1, x2, y2 = _bbox_from_grid(xs, ys)
        area = abs((x2 - x1) * (y2 - y1))
        if area <= 0:
            continue
        if best is None or area < best[2]:
            best = (xs, ys, area)
    if best is None:
        return [], []
    return best[0], best[1]


def _entity_in_rect(e: Any, rect: Tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = rect
    et = e.dxftype()
    try:
        if et in ("TEXT", "MTEXT", "INSERT"):
            px, py = vec3_to_xy(e.dxf.insert)
            return _point_in_rect(px, py, rect)
        if et == "LINE":
            x1e, y1e = vec3_to_xy(e.dxf.start)
            x2e, y2e = vec3_to_xy(e.dxf.end)
            return _point_in_rect(x1e, y1e, rect) or _point_in_rect(x2e, y2e, rect)
        if et in ("LWPOLYLINE", "POLYLINE"):
            pts = []
            if hasattr(e, "get_points"):
                try:
                    pts = list(e.get_points("xy"))
                except Exception:
                    pts = []
            if not pts and hasattr(e, "points"):
                pts = list(getattr(e, "points", []))
            for (px, py) in pts:
                if _point_in_rect(px, py, rect):
                    return True
            return False
        if et in ("CIRCLE", "ARC", "ELLIPSE"):
            cx, cy = vec3_to_xy(e.dxf.center)
            return _point_in_rect(cx, cy, rect)
    except Exception:
        return False
    return False


def _export_debug_clip(doc: Any, layout: Any, rect: Tuple[float, float, float, float], out_path: str, layer_filter: Optional[str] = None) -> None:
    new_doc = ezdxf.new(setup=True)
    new_msp = new_doc.modelspace()
    importer = Importer(doc, new_doc)
    count = 0
    try:
        for e in layout:
            try:
                # layer filtering if required
                if layer_filter:
                    try:
                        if str(getattr(e.dxf, "layer", "")).strip().upper() != layer_filter.upper():
                            continue
                    except Exception:
                        continue
                if _entity_in_rect(e, rect):
                    importer.import_entity(e, new_msp)
                    count += 1
            except Exception:
                continue
        importer.finalize()
    except Exception:
        pass
    try:
        new_doc.saveas(out_path)
    except PermissionError:
        base, ext = os.path.splitext(out_path)
        alt = f"{base}_{int(time.time())}{ext}"
        new_doc.saveas(alt)


def export_tables_structured(doc: Any, out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for layout in doc.layouts:
        space = layout
        layout_name = layout.name
        # Detect axis-aligned segments on TEXT layer only
        v_segs, h_segs = _axis_aligned_segments(space, layer_filter="TEXT")
        clusters = _cluster_segments(v_segs, h_segs)
        if not clusters:
            continue
        idx = 0
        for comp_v, comp_h in clusters:
            vs = [v_segs[i] for i in comp_v]
            hs = [h_segs[j] for j in comp_h]
            xs, ys = _grid_from_segments(vs, hs)
            if len(xs) < 3 or len(ys) < 3:
                continue
            outer_rect = (min(xs), min(ys), max(xs), max(ys))
            idx += 1
            dxf_name = f"table_{sanitize_filename(layout_name)}_grid_{idx}.dxf"
            dxf_path = os.path.join(out_dir, dxf_name)
            # Export all entities (all layers) inside the detected rect
            _export_debug_clip(doc, space, outer_rect, dxf_path)
            written.append(dxf_path)
    return written


def pick_input_dxf(default_path: str) -> str:
    if os.path.exists(default_path):
        return default_path
    # Auto-detect a DXF in BASE_DIR
    candidates = [f for f in os.listdir(BASE_DIR) if f.lower().endswith(".dxf")]
    # Priority: exact '我的桩位图.dxf' -> contains '桩位图' -> others (alphabetical)
    priority = [
        "我的桩位图.dxf",
    ]
    for p in priority:
        if p in candidates:
            return os.path.join(BASE_DIR, p)
    for f in candidates:
        if "桩位图" in f:
            return os.path.join(BASE_DIR, f)
    if candidates:
        candidates.sort()
        return os.path.join(BASE_DIR, candidates[0])
    return default_path  # will fail later with clear message


def main() -> None:
    input_path = pick_input_dxf(INPUT_DXF)
    if not os.path.exists(input_path):
        print(f"DXF not found: {input_path}")
        sys.exit(1)

    print(f"Using DXF: {os.path.basename(input_path)}")
    doc = ezdxf.readfile(input_path)

    tables_dir = os.path.join(BASE_DIR, "dxf_tables")
    table_dxfs = export_tables_structured(doc, tables_dir)
    if table_dxfs:
        print("Exported table DXFs:")
        for p in table_dxfs:
            print(p)


if __name__ == "__main__":
    main() 