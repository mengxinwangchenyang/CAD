#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Annotate uplift force texts from CSV onto the pile plan DXF.

- Reads outputs/原设计桩基抗拔反力.csv
- Filters rows where Layer is "7551" or "7553"
- Writes the row's Text at the entity coordinates onto 桩位图.dxf
- Saves annotated DXF under outputs folder

Notes:
- Coordinates are auto-detected from common columns (X/Y, InsertX/InsertY, CenterX/CenterY, StartX/StartY)
- Parameters configured via direct assignment below
- Requires: ezdxf (pip install ezdxf)

更新：已完全去除“自动偏移”功能（只保留手动硬编码偏移）；保留 7505 调试覆盖。
"""

from __future__ import annotations

import csv
import os
import sys
from typing import Dict, List, Optional, Tuple

# =====================
# Configuration
# =====================
PATH_INPUT_CSV = os.path.join(".", "outputs", "原设计桩基抗拔反力.csv")
PATH_INPUT_DXF = os.path.join(".", "桩位图.dxf")
PATH_OUTPUT_DXF = os.path.join(".", "outputs", "桩位图_抗拔反力_标注.dxf")

TARGET_LAYERS = {"7551", "7553"}

# 硬编码文本偏移（不再提供自动偏移）
TEXT_HEIGHT = 250.0
TEXT_OFFSET_X = -1145010.5115
TEXT_OFFSET_Y = -1147360.8348
USE_MTEXT = False
MTEXT_WIDTH = 2000.0

# 调试覆盖：把参考 DXF 的 7505 标记（几何中心）画到输出，便于对齐检查
ENABLE_DEBUG_OVERLAY_7505 = True
DEBUG_OVERLAY_LAYER_NAME = "7505"  # 按用户要求，标到 7505 图层
DEBUG_SQUARE_SIZE = 300.0  # 正方形边长，按图纸单位（可根据需要调整）

# 用于调试覆盖：参考 DXF 与图层名
PATH_REF_DXF_FOR_DEBUG = os.path.join(".", "原设计桩基抗拔反力.dxf")
REF_MARKER_LAYER = "7505"

# Center extraction options for markers
USE_SQUARE_FILTER = True
SQUARE_RATIO_TOL = 0.35  # |w-h|/max(w,h) <= tol considered near-square
MIN_SQUARE_SIZE = 1.0

# Field candidates
FIELD_LAYER = ["Layer", "图层"]
FIELD_TEXT = ["Text", "文字", "内容"]
COORD_CANDIDATES: List[Tuple[List[str], List[str]]] = [
    (["X", "InsertX", "CenterX", "StartX", "EndX", "x"], ["Y", "InsertY", "CenterY", "StartY", "EndY", "y"]),
]


def _try_import_ezdxf():
    try:
        import ezdxf  # type: ignore
        return ezdxf
    except Exception as exc:
        print("[ERROR] 需要安装 ezdxf 库：pip install ezdxf", file=sys.stderr)
        print(f"[DETAIL] {exc}", file=sys.stderr)
        raise


# --- Geometry helpers (used by debug overlay) ---

def _safe_point(p) -> Optional[Tuple[float, float]]:
    try:
        x = float(p[0]); y = float(p[1])
        return (x, y)
    except Exception:
        try:
            x = float(getattr(p, "x", None)); y = float(getattr(p, "y", None))
            return (x, y)
        except Exception:
            return None


def _bbox_from_points(points: List[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _center_from_bbox(bb: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = bb
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _square_like(bb: Tuple[float, float, float, float]) -> bool:
    if not USE_SQUARE_FILTER:
        return True
    x0, y0, x1, y1 = bb
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)
    if w < MIN_SQUARE_SIZE or h < MIN_SQUARE_SIZE:
        return False
    m = max(w, h)
    if m <= 0.0:
        return False
    return abs(w - h) / m <= SQUARE_RATIO_TOL


def _iter_virt_points_of_entity(e) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    try:
        dxftype = e.dxftype()
        if dxftype == "LWPOLYLINE":
            try:
                for vx in e.vertices():
                    pts.append((float(vx[0]), float(vx[1])))
            except Exception:
                pass
            return pts
        if dxftype in ("TEXT", "MTEXT"):
            p = getattr(e.dxf, "insert", None) or getattr(e.dxf, "align_point", None)
            sp = _safe_point(p)
            if sp:
                pts.append(sp)
            return pts
        if dxftype in ("CIRCLE", "ARC", "ELLIPSE"):
            p = getattr(e.dxf, "center", None)
            sp = _safe_point(p)
            if sp:
                pts.append(sp)
            return pts
        if dxftype == "LINE":
            s = _safe_point(getattr(e.dxf, "start", None))
            ep = _safe_point(getattr(e.dxf, "end", None))
            if s and ep:
                pts.extend([s, ep])
            elif s:
                pts.append(s)
            elif ep:
                pts.append(ep)
            return pts
        if dxftype == "POINT":
            p = _safe_point(getattr(e.dxf, "location", None))
            if p:
                pts.append(p)
            return pts
        if dxftype == "HATCH":
            try:
                paths = list(getattr(e, "paths", []) or [])
            except Exception:
                paths = []
            if paths:
                try:
                    verts = list(getattr(paths[0], "vertices", []) or [])
                    for v in verts:
                        sp = _safe_point(v)
                        if sp:
                            pts.append(sp)
                except Exception:
                    pass
            if not pts:
                try:
                    seeds = list(getattr(e, "seeds", []) or [])
                    for s in seeds:
                        sp = _safe_point(s)
                        if sp:
                            pts.append(sp)
                except Exception:
                    pass
            return pts
        for name in ("insert", "location", "center", "start"):
            if hasattr(e.dxf, name):
                sp = _safe_point(getattr(e.dxf, name))
                if sp:
                    pts.append(sp)
        return pts
    except Exception:
        return pts


def _entity_center(e) -> Optional[Tuple[float, float]]:
    """Compute geometric center for common entity types; for INSERT expand virtual entities."""
    try:
        dxftype = e.dxftype()
        if dxftype == "INSERT":
            try:
                vpts: List[Tuple[float, float]] = []
                for ve in e.virtual_entities():
                    vpts.extend(_iter_virt_points_of_entity(ve))
                bb = _bbox_from_points(vpts)
                if bb and _square_like(bb):
                    return _center_from_bbox(bb)
                p = _safe_point(getattr(e.dxf, "insert", None))
                return p
            except Exception:
                p = _safe_point(getattr(e.dxf, "insert", None))
                return p
        if dxftype == "LWPOLYLINE":
            pts = _iter_virt_points_of_entity(e)
            bb = _bbox_from_points(pts)
            if bb and _square_like(bb):
                return _center_from_bbox(bb)
            return None
        if dxftype == "HATCH":
            pts = _iter_virt_points_of_entity(e)
            bb = _bbox_from_points(pts)
            if bb and _square_like(bb):
                return _center_from_bbox(bb)
            return None
        pts = _iter_virt_points_of_entity(e)
        bb = _bbox_from_points(pts)
        if bb:
            return _center_from_bbox(bb)
        return None
    except Exception:
        return None


def _collect_layer_centers(doc, layer: str, max_points: int = 0) -> List[Tuple[float, float]]:
    centers: List[Tuple[float, float]] = []
    try:
        msp = doc.modelspace()
        for e in msp:
            try:
                if getattr(e.dxf, "layer", None) != layer:
                    continue
                c = _entity_center(e)
                if c is not None:
                    centers.append(c)
                    if max_points and len(centers) >= max_points:
                        break
            except Exception:
                continue
    except Exception:
        return centers
    return centers


# --- Drawing helpers ---

def _draw_square(msp, cx: float, cy: float, size: float, layer_name: str) -> None:
    """Draw a small square centered at (cx, cy) on the specified layer."""
    h = size / 2.0
    pts = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h), (cx - h, cy - h)]
    try:
        msp.add_lwpolyline(pts, dxfattribs={"layer": layer_name})
    except Exception:
        try:
            msp.add_polyline2d(pts, dxfattribs={"layer": layer_name})
        except Exception:
            pass


def _read_csv(path: str) -> List[Dict[str, str]]:
    encodings = ["utf-8-sig", "utf-8", "gbk"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                rows = [dict({k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}) for row in reader]
                return rows
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            raise
    raise UnicodeDecodeError("", b"", 0, 1, "Unable to decode CSV with common encodings")


def _get_header_name(headers: List[str], candidates: List[str]) -> Optional[str]:
    lower_map = {h.strip().casefold(): h for h in headers}
    for cand in candidates:
        ck = cand.strip().casefold()
        if ck in lower_map:
            return lower_map[ck]
    return None


def _detect_coord_fields(headers: List[str]) -> Optional[Tuple[str, str]]:
    lower_map = {h.strip().casefold(): h for h in headers}
    for x_list, y_list in COORD_CANDIDATES:
        x_key: Optional[str] = None
        y_key: Optional[str] = None
        for x in x_list:
            if x.strip().casefold() in lower_map:
                x_key = lower_map[x.strip().casefold()]
                break
        for y in y_list:
            if y.strip().casefold() in lower_map:
                y_key = lower_map[y.strip().casefold()]
                break
        if x_key and y_key:
            return (x_key, y_key)
    return None


def _to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    val = text.strip().replace(",", "")
    if val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def collect_annotations(rows: List[Dict[str, str]]) -> List[Tuple[float, float, str, str]]:
    """
    Return list of (x, y, text, layer)
    """
    if not rows:
        return []
    headers = list(rows[0].keys())
    layer_field = _get_header_name(headers, FIELD_LAYER)
    text_field = _get_header_name(headers, FIELD_TEXT)
    coord_fields = _detect_coord_fields(headers)
    if not layer_field or not text_field or not coord_fields:
        raise KeyError("无法识别 CSV 的 Layer/Text/坐标字段")
    x_field, y_field = coord_fields

    annotations: List[Tuple[float, float, str, str]] = []
    for row in rows:
        layer = (row.get(layer_field) or "").strip()
        if layer not in TARGET_LAYERS:
            continue
        text = (row.get(text_field) or "").strip()
        if text == "":
            continue
        x = _to_float(row.get(x_field))
        y = _to_float(row.get(y_field))
        if x is None or y is None:
            coord_fields_row = _detect_coord_fields(list(row.keys()))
            if coord_fields_row:
                x = _to_float(row.get(coord_fields_row[0]))
                y = _to_float(row.get(coord_fields_row[1]))
        if x is None or y is None:
            continue
        annotations.append((x, y, text, layer))
    return annotations


def annotate_dxf(input_dxf: str, output_dxf: str, annotations: List[Tuple[float, float, str, str]], overlay_points: Optional[List[Tuple[float, float]]] = None, overlay_layer_name: str = DEBUG_OVERLAY_LAYER_NAME, overlay_square_size: float = DEBUG_SQUARE_SIZE) -> None:
    ezdxf = _try_import_ezdxf()
    doc = ezdxf.readfile(input_dxf)
    msp = doc.modelspace()

    # Ensure XDATA appid exists
    try:
        doc.appids.new("ANNOTATION")
    except Exception:
        pass

    # Ensure force text target layer exists
    try:
        if "TEXT反力" not in doc.layers:
            doc.layers.add("TEXT反力")
    except Exception:
        pass

    # Ensure layer exists for annotation texts
    try:
        if "DEBUG表格" not in doc.layers:
            doc.layers.add("DEBUG表格")
    except Exception:
        pass

    for x, y, text, layer in annotations:
        px = x + TEXT_OFFSET_X
        py = y + TEXT_OFFSET_Y
        if USE_MTEXT:
            mtext = msp.add_mtext(text, dxfattribs={"height": TEXT_HEIGHT, "width": MTEXT_WIDTH, "layer": "TEXT反力"})
            mtext.dxf.insert = (px, py)
            try:
                mtext.set_xdata("ANNOTATION", [(1000, f"SRC_LAYER={layer}")])
            except Exception:
                pass
        else:
            ent = msp.add_text(text, dxfattribs={"height": TEXT_HEIGHT, "layer": "TEXT反力"})
            ent.dxf.insert = (px, py)
            try:
                ent.set_xdata("ANNOTATION", [(1000, f"SRC_LAYER={layer}")])
            except Exception:
                pass

    # Debug overlay of reference markers onto output
    if ENABLE_DEBUG_OVERLAY_7505 and overlay_points:
        try:
            doc.layers.new(overlay_layer_name)
        except Exception:
            pass
        for (rx, ry) in overlay_points:
            px = rx + TEXT_OFFSET_X
            py = ry + TEXT_OFFSET_Y
            _draw_square(msp, px, py, overlay_square_size, overlay_layer_name)

    doc.saveas(output_dxf)


def main() -> None:
    print("[INFO] 读取: " + PATH_INPUT_CSV)
    rows = _read_csv(PATH_INPUT_CSV)
    annotations = collect_annotations(rows)
    print(f"[INFO] 目标层 {sorted(list(TARGET_LAYERS))}，待标注数量: {len(annotations)}")
    if not annotations:
        print("[WARN] 没有可标注的文本")
        return

    # 不再进行自动偏移；完全依赖手动 TEXT_OFFSET_X / TEXT_OFFSET_Y

    # Collect overlay points from reference DXF's 7505 layer (centers)
    overlay_points: List[Tuple[float, float]] = []
    if ENABLE_DEBUG_OVERLAY_7505:
        try:
            ezdxf = _try_import_ezdxf()
            ref_doc = ezdxf.readfile(PATH_REF_DXF_FOR_DEBUG)
            overlay_points = _collect_layer_centers(ref_doc, REF_MARKER_LAYER, max_points=0)
            print(f"[INFO] 调试覆盖中心点数量(7505): {len(overlay_points)}")
        except Exception:
            print("[WARN] 无法读取参考DXF以写出调试覆盖。")

    os.makedirs(os.path.dirname(PATH_OUTPUT_DXF), exist_ok=True)
    print("[INFO] 标注到: " + PATH_INPUT_DXF)
    annotate_dxf(PATH_INPUT_DXF, PATH_OUTPUT_DXF, annotations, overlay_points=overlay_points)
    print("[OK] 已输出: " + PATH_OUTPUT_DXF)
    print("\n[SUMMARY]")
    for x, y, text, layer in annotations[:20]:
        print(f" - L{layer} @({x:.2f},{y:.2f}) -> {text}")
    if len(annotations) > 20:
        print(f" ... 以及 {len(annotations) - 20} 条更多标注")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print(f"[ERROR] 文件不存在: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"[ERROR] 字段缺失: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[ERROR] 处理失败: {e}", file=sys.stderr)
        sys.exit(3)
