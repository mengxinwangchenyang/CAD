#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Annotate DXF with uplift force text based on CSV mappings.

- Reads 桩位图_抗拔反力_标注.csv and table_Model_grid_1.csv
- Matches 桩位图_抗拔反力_标注.csv's ModuleName to table_Model_grid_1.csv's "桩图例"
- For each matching block, finds the nearest "TEXT反力" layer text
- Annotates the matched blocks with the text information
- Saves to a new DXF file

Notes:
- Coordinates are auto-detected from common column names (X/Y, InsertX/InsertY, etc.)
- Configurable parameters are set via direct assignment below
- Requires: ezdxf (pip install ezdxf)
"""

from __future__ import annotations

import csv
import os
import sys
import math
import re
from typing import Dict, List, Optional, Tuple

# =====================
# Configuration (edit these as needed)
# =====================
PATH_PILE_CSV = r".\outputs\桩位图_抗拔反力_标注.csv"
PATH_TABLE_CSV = r".\dxf_tables\table_Model_grid_1.csv"
PATH_INPUT_DXF = r".\桩位图_抗拔反力_标注.dxf"
PATH_OUTPUT_DXF = r".\桩位图_抗拔反力_标注_文字.dxf"
PATH_OUTPUT_CSV = r".\outputs\桩位图_抗拔反力_标注_文字.csv"

# Text placement & style
TEXT_HEIGHT = 250.0  # Adjust based on drawing units
TEXT_OFFSET_X = 1000.0  # Offset from element location to avoid overlap
TEXT_OFFSET_Y = 1000.0
USE_MTEXT = False  # If True, use MTEXT instead of TEXT
MTEXT_WIDTH = 2000.0  # Effective only when USE_MTEXT is True

# Field names (case-insensitive match). Multiple candidates supported
FIELD_MODULE_NAME_CANDIDATES = [
    "ModuleName",
    "模块名称",
    "桩图例",
]
FIELD_MODULE_NOW_ID_CANDIDATES = [
    "ModuleNowID",
    "模块ID",
    "桩ID",
]
FIELD_TABLE_KEY_CANDIDATES = [
    "桩图例",
    "ModuleName",
]

# Coordinate candidates: list of (x_candidates, y_candidates)
COORDINATE_CANDIDATE_PAIRS: List[Tuple[List[str], List[str]]] = [
    (["InsertX", "插入X", "位置X", "PosX", "X", "x", "center_x", "CenterX", "中心X", "cx"],
     ["InsertY", "插入Y", "位置Y", "PosY", "Y", "y", "center_y", "CenterY", "中心Y", "cy"]),
]


def _try_import_ezdxf():
    try:
        import ezdxf  # type: ignore
        return ezdxf
    except Exception as exc:  # pragma: no cover
        print("[ERROR] 需要安装 ezdxf 库：pip install ezdxf", file=sys.stderr)
        print(f"[DETAIL] {exc}", file=sys.stderr)
        raise


def _read_csv_dicts(path: str) -> List[Dict[str, str]]:
    # Try UTF-8 with BOM first, then GBK as fallback
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
    # If we get here, decoding failed
    raise UnicodeDecodeError("", b"", 0, 1, "Unable to decode CSV with common encodings")


def _casefold(s: Optional[str]) -> str:
    return (s or "").strip().casefold()


def _get_first_field(row: Dict[str, str], candidates: List[str]) -> Optional[str]:
    lower_map = {k.strip().casefold(): k for k in row.keys()}
    for cand in candidates:
        ck = cand.strip().casefold()
        if ck in lower_map:
            # Return the original header name, not the row value
            return lower_map[ck]
    return None


def _detect_coordinate_fields(headers: List[str]) -> Optional[Tuple[str, str]]:
    lower_headers = {h.strip().casefold(): h for h in headers}
    for x_cands, y_cands in COORDINATE_CANDIDATE_PAIRS:
        x_key: Optional[str] = None
        y_key: Optional[str] = None
        for x in x_cands:
            xl = x.strip().casefold()
            if xl in lower_headers:
                x_key = lower_headers[xl]
                break
        for y in y_cands:
            yl = y.strip().casefold()
            if yl in lower_headers:
                y_key = lower_headers[yl]
                break
        if x_key and y_key:
            return (x_key, y_key)
    return None


def _parse_float_safe(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_table_mapping(table_rows: List[Dict[str, str]]) -> Dict[str, str]:
    """Build mapping from 桩图例 to module names for matching"""
    mapping: Dict[str, str] = {}
    if not table_rows:
        return mapping
    
    # Resolve table fields
    proto_row = table_rows[0]
    key_field = _get_first_field(proto_row, FIELD_TABLE_KEY_CANDIDATES)
    if not key_field:
        raise KeyError("无法在表CSV中找到 '桩图例' 字段")
    
    for row in table_rows:
        key = row.get(key_field)
        if key is None:
            continue
        k = _casefold(key)
        if k and k not in mapping:
            mapping[k] = key.strip()  # Store the original key for reference
    return mapping


def find_text_force_annotations(pile_rows: List[Dict[str, str]]) -> List[Tuple[str, float, float]]:
    """Find all TEXT反力 layer text annotations with coordinates"""
    text_annotations: List[Tuple[str, float, float]] = []
    
    for row in pile_rows:
        layer = row.get("Layer", "").strip()
        if layer == "TEXT反力":
            text_content = row.get("Text", "").strip()
            x_val = _parse_float_safe(row.get("X"))
            y_val = _parse_float_safe(row.get("Y"))
            
            if text_content and x_val is not None and y_val is not None:
                text_annotations.append((text_content, x_val, y_val))
    
    return text_annotations


def calculate_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Calculate Euclidean distance between two points"""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def is_valid_force_text(text: str) -> Optional[str]:
    """
    Check if text matches the pattern a(b) where a and b are numbers (can be negative).
    Returns the 'a' value if valid, None otherwise.
    """
    # Pattern to match a(b) where a and b are numbers (including negative numbers)
    pattern = r'(-?\d+(?:\.\d+)?)\(-?\d+(?:\.\d+)?\)'
    match = re.search(pattern, text)
    if match:
        return match.group(1)  # Return the 'a' value
    return None


def find_nearest_valid_text(block_x: float, block_y: float, text_annotations: List[Tuple[str, float, float]]) -> Optional[str]:
    """Find the nearest valid TEXT反力 annotation to a given block position"""
    if not text_annotations:
        return None
    
    # Sort annotations by distance
    valid_annotations = []
    for text_content, text_x, text_y in text_annotations:
        force_value = is_valid_force_text(text_content)
        if force_value is not None:
            distance = calculate_distance(block_x, block_y, text_x, text_y)
            valid_annotations.append((distance, force_value))
    
    if not valid_annotations:
        return None
    
    # Sort by distance and return the nearest valid text
    valid_annotations.sort(key=lambda x: x[0])
    nearest_force_value = valid_annotations[0][1]
    
    return f"Nt={nearest_force_value}"


# 新增：从标注文本中提取纯数值反力（去掉前缀）
def _extract_force_from_annotation_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("Nt="):
        return t[3:].strip()
    # 兜底：匹配形如 Nt=123 或 Nt = -45.6
    m = re.match(r"Nt\s*=\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        return m.group(1)
    return None


# 新增：由收集到的注记构建 模块ID->反力 数值的映射
def build_module_id_to_force_map(
    annotations: List[Tuple[str, float, float, str]]
) -> Dict[str, str]:
    id_to_force: Dict[str, str] = {}
    for module_id, _x, _y, text in annotations:
        val = _extract_force_from_annotation_text(text)
        if val is not None:
            id_to_force[module_id] = val
    return id_to_force


# 新增：写出在原CSV基础上追加“反力”列的新CSV
def write_augmented_csv_with_force(
    pile_rows: List[Dict[str, str]],
    output_csv_path: str,
    id_to_force: Dict[str, str],
) -> None:
    if not pile_rows:
        # 仍写出仅包含表头（如果无法检测则写出只有“反力”一列）
        os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)
        try:
            with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["反力"])
                writer.writeheader()
        except Exception:
            pass
        return

    # 保留原有列顺序，并在末尾追加“反力”列（如已存在则沿用位置）
    original_fieldnames = list(pile_rows[0].keys())
    if "反力" in original_fieldnames:
        fieldnames = original_fieldnames
    else:
        fieldnames = original_fieldnames + ["反力"]

    # 检测模块ID字段名
    id_field = _get_first_field(pile_rows[0], FIELD_MODULE_NOW_ID_CANDIDATES)
    if not id_field:
        raise KeyError("无法在桩位图CSV中找到 'ModuleNowID' 字段以写入反力列")

    # 确保目录存在
    os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)

    with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in pile_rows:
            module_id = (row.get(id_field) or "").strip()
            force_val = id_to_force.get(module_id, "")
            out_row = dict(row)
            out_row["反力"] = force_val
            writer.writerow(out_row)


# 新增：从增广CSV（包含“反力”列）构建用于DXF的标注
def collect_annotations_from_augmented_csv(
    augmented_rows: List[Dict[str, str]]
) -> List[Tuple[str, float, float, str]]:
    annotations: List[Tuple[str, float, float, str]] = []
    if not augmented_rows:
        return annotations

    # 检测必要列
    proto = augmented_rows[0]
    id_field = _get_first_field(proto, FIELD_MODULE_NOW_ID_CANDIDATES)
    if not id_field:
        raise KeyError("无法在增广CSV中找到 'ModuleNowID' 字段")

    coord_fields = _detect_coordinate_fields(list(proto.keys()))
    if not coord_fields:
        # 尝试跨行侦测
        for row in augmented_rows:
            coord_fields = _detect_coordinate_fields(list(row.keys()))
            if coord_fields:
                break
    if not coord_fields:
        raise KeyError("无法在增广CSV中检测到坐标列 (X/Y、InsertX/InsertY 等)")
    x_field, y_field = coord_fields

    # 定位“反力”字段名（保持原表头名）
    force_field: Optional[str] = None
    for k in proto.keys():
        if k.strip() == "反力":
            force_field = k
            break
    if not force_field:
        raise KeyError("增广CSV中未找到‘反力’列")

    seen_ids: set[str] = set()
    for row in augmented_rows:
        module_id = (row.get(id_field) or "").strip()
        if not module_id or module_id in seen_ids:
            continue
        x_val = _parse_float_safe(row.get(x_field))
        y_val = _parse_float_safe(row.get(y_field))
        force_val = (row.get(force_field) or "").strip()
        if x_val is None or y_val is None:
            continue
        if force_val == "":
            # 无反力值则不标注
            continue
        annotations.append((module_id, x_val, y_val, f"Nt={force_val}"))
        seen_ids.add(module_id)

    return annotations


def collect_annotations(
    pile_rows: List[Dict[str, str]],
    table_map: Dict[str, str],
) -> List[Tuple[str, float, float, str]]:
    """
    Return list of (module_now_id, x, y, annotation_text)
    ensuring one per ModuleNowID.
    """
    annotations: List[Tuple[str, float, float, str]] = []
    if not pile_rows:
        return annotations

    # First, find all TEXT反力 annotations
    text_annotations = find_text_force_annotations(pile_rows)
    print(f"[INFO] 找到 {len(text_annotations)} 个TEXT反力标注")
    
    # Debug: show some examples of text annotations
    valid_count = 0
    for text_content, _, _ in text_annotations[:10]:
        force_value = is_valid_force_text(text_content)
        if force_value is not None:
            valid_count += 1
            print(f"[DEBUG] 有效文本: '{text_content}' -> 力值: {force_value}")
        else:
            print(f"[DEBUG] 无效文本: '{text_content}'")
    print(f"[INFO] 前10个标注中有 {valid_count} 个有效")

    # Resolve key field names
    proto = pile_rows[0]
    module_field = _get_first_field(proto, FIELD_MODULE_NAME_CANDIDATES)
    id_field = _get_first_field(proto, FIELD_MODULE_NOW_ID_CANDIDATES)
    if not module_field or not id_field:
        raise KeyError("无法在桩位图CSV中找到 'ModuleName' 或 'ModuleNowID' 字段")

    coord_fields = _detect_coordinate_fields(list(proto.keys()))
    if not coord_fields:
        # Try scan across rows to find any row that reveals headers better
        for row in pile_rows:
            coord_fields = _detect_coordinate_fields(list(row.keys()))
            if coord_fields:
                break
    if not coord_fields:
        raise KeyError("无法在桩位图CSV中检测到坐标列 (X/Y、InsertX/InsertY 等)")
    x_field, y_field = coord_fields

    seen_ids: set[str] = set()
    for row in pile_rows:
        module_name = row.get(module_field)
        module_id = row.get(id_field)
        x_val = _parse_float_safe(row.get(x_field))
        y_val = _parse_float_safe(row.get(y_field))
        
        if module_id is None or module_name is None:
            continue
        module_id = module_id.strip()
        if module_id == "" or module_id in seen_ids:
            continue
        if x_val is None or y_val is None:
            # Skip if no coordinates for this row
            continue
            
        lookup_key = _casefold(module_name)
        # Check if this module name matches any in our table mapping
        if lookup_key in table_map:
            # Find the nearest valid TEXT反力 annotation
            nearest_text = find_nearest_valid_text(x_val, y_val, text_annotations)
            if nearest_text:
                annotations.append((module_id, x_val, y_val, nearest_text))
                seen_ids.add(module_id)

    return annotations


def annotate_dxf(
    input_dxf_path: str,
    output_dxf_path: str,
    annotations: List[Tuple[str, float, float, str]],
    text_height: float = TEXT_HEIGHT,
    offset_x: float = TEXT_OFFSET_X,
    offset_y: float = TEXT_OFFSET_Y,
    use_mtext: bool = USE_MTEXT,
    mtext_width: float = MTEXT_WIDTH,
) -> None:
    ezdxf = _try_import_ezdxf()
    doc = ezdxf.readfile(input_dxf_path)
    msp = doc.modelspace()

    # Ensure XDATA appid exists
    try:
        doc.appids.new("UPLIFT_FORCE_ANNOTATION")
    except Exception:
        pass

    # Ensure layer exists for annotation texts
    try:
        if "DEBUG反力" not in doc.layers:
            doc.layers.add("DEBUG反力")
    except Exception:
        pass

    for module_id, x, y, text in annotations:
        px = x + offset_x
        py = y + offset_y
        if use_mtext:
            mtext = msp.add_mtext(text, dxfattribs={"height": text_height, "width": mtext_width, "color": 5, "layer": "DEBUG反力"})  # Color 5 = Blue
            # Place by insert point for wider compatibility
            mtext.dxf.insert = (px, py)
            try:
                mtext.set_xdata("UPLIFT_FORCE_ANNOTATION", [(1000, f"ModuleNowID={module_id}")])
            except Exception:
                pass
        else:
            ent = msp.add_text(text, dxfattribs={"height": text_height, "color": 5, "layer": "DEBUG反力"})  # Color 5 = Blue
            # Place by insert point for compatibility with ezdxf versions lacking set_pos
            ent.dxf.insert = (px, py)
            try:
                ent.set_xdata("UPLIFT_FORCE_ANNOTATION", [(1000, f"ModuleNowID={module_id}")])
            except Exception:
                pass

    doc.saveas(output_dxf_path)


def main() -> None:
    print("[INFO] 开始处理抗拔反力标注……")
    print(f"[INFO] 读取桩位图抗拔反力数据: {PATH_PILE_CSV}")
    pile_rows = _read_csv_dicts(PATH_PILE_CSV)
    print(f"[INFO] 读取表数据: {PATH_TABLE_CSV}")
    table_rows = _read_csv_dicts(PATH_TABLE_CSV)

    table_map = build_table_mapping(table_rows)
    print(f"[INFO] 表映射数量: {len(table_map)}")

    annotations = collect_annotations(pile_rows, table_map)
    print(f"[INFO] 计划标注数量(去重后): {len(annotations)}")

    # 新增：无论是否找到注记，都输出带“反力”列的CSV（无值则留空）
    try:
        id_to_force = build_module_id_to_force_map(annotations)
        print(f"[INFO] 写出带‘反力’列的CSV: {PATH_OUTPUT_CSV}")
        write_augmented_csv_with_force(pile_rows, PATH_OUTPUT_CSV, id_to_force)
        print(f"[OK] 已保存CSV: {PATH_OUTPUT_CSV}")
    except Exception as exc:
        print(f"[WARN] 写出CSV失败: {exc}")

    # 新逻辑：基于增广CSV生成DXF标注
    print(f"[INFO] 读取用于标注的增广CSV: {PATH_OUTPUT_CSV}")
    augmented_rows = _read_csv_dicts(PATH_OUTPUT_CSV)
    csv_annotations = collect_annotations_from_augmented_csv(augmented_rows)
    print(f"[INFO] 基于CSV的计划标注数量: {len(csv_annotations)}")

    if not csv_annotations:
        print("[WARN] 没有可标注的数据（CSV中‘反力’为空），退出。")
        return

    # Ensure output directory exists
    os.makedirs(os.path.dirname(PATH_OUTPUT_DXF), exist_ok=True)
    print(f"[INFO] 读取并标注 DXF: {PATH_INPUT_DXF}")
    annotate_dxf(
        input_dxf_path=PATH_INPUT_DXF,
        output_dxf_path=PATH_OUTPUT_DXF,
        annotations=csv_annotations,
    )
    print(f"[OK] 已保存标注文件: {PATH_OUTPUT_DXF}")

        # Summary lines
    print("\n[SUMMARY]")
    for module_id, x, y, text in csv_annotations[:20]:
            print(f" - {module_id} @({x:.2f},{y:.2f}) -> {text}")
    if len(csv_annotations) > 20:
        print(f" ... 以及 {len(csv_annotations) - 20} 条更多标注")


if __name__ == "__main__":
    main()