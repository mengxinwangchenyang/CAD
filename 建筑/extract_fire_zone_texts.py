import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional

OUTPUT_DIR = Path("outputs")
INPUT_CSV = OUTPUT_DIR / "建筑平面图_人防平面.csv"
TARGET_LAYER = "防火分区面积"

# 需要打印的字段（尽量简洁，覆盖定位与文本内容）
PRINT_FIELDS = [
    "ModuleId", "Layer", "SubClass", "Text", "X", "Y", "Z",
    "EntityHandle", "OwnerHandle", "Space"
]

AREA_PATTERN = re.compile(r"S\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def collect_target_module_ids(csv_path: Path, target_layer: str) -> set:
    """收集在目标图层上出现过的 ModuleId（包括块展开的虚拟实体）。"""
    mids = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            layer = (row.get("Layer", "") or "").strip()
            if layer != target_layer:
                continue
            mid = (row.get("ModuleId", "") or "").strip()
            if mid:
                mids.add(mid)
    return mids


def row_is_text_like(row: Dict[str, str]) -> bool:
    sc = (row.get("SubClass", "") or "").strip().upper()
    if sc in ("TEXT", "MTEXT"):
        return True
    # INSERT 行本身保存了属性汇总在 Text 字段中
    if sc == "INSERT":
        return True
    return False


def _safe_float(row: Dict[str, str], key: str) -> Optional[float]:
    try:
        v = row.get(key, "")
        if v == "" or v is None:
            return None
        return float(v)
    except Exception:
        return None


def _is_zone_name(text: str) -> bool:
    return isinstance(text, str) and text.strip().startswith("防火分区")


def _parse_area(text: str) -> Optional[float]:
    if not isinstance(text, str):
        return None
    m = AREA_PATTERN.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def pair_zones_and_areas(rows: List[Dict[str, str]]):
    """在已过滤的 rows 中，将“防火分区X”与最近的“S=...”配对。
    返回 (pairs, total_area)
    pairs: [{"name": str, "area_m2": float, "name_xy": {x,y}, "area_xy": {x,y}}]
    """
    name_nodes: List[Tuple[int, float, float, str]] = []  # (idx, x, y, name)
    area_nodes: List[Tuple[int, float, float, float]] = []  # (idx, x, y, area)

    for i, r in enumerate(rows):
        text = (r.get("Text", "") or "").strip()
        x = _safe_float(r, "X")
        y = _safe_float(r, "Y")
        if x is None or y is None:
            continue
        if _is_zone_name(text):
            name_nodes.append((i, x, y, text))
            continue
        area_val = _parse_area(text)
        if area_val is not None:
            area_nodes.append((i, x, y, area_val))

    # 贪心最近邻配对（每个面积只能用一次）
    used_area = set()
    pairs = []
    for ni, nx, ny, ntext in name_nodes:
        best_j = None
        best_d2 = None
        for aj, ax, ay, aval in area_nodes:
            if aj in used_area:
                continue
            dx = ax - nx
            dy = ay - ny
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_j = aj
        if best_j is not None:
            used_area.add(best_j)
            # 取该面积节点详细信息
            for aj, ax, ay, aval in area_nodes:
                if aj == best_j:
                    pairs.append({
                        "name": ntext,
                        "area_m2": aval,
                        "name_xy": {"x": round(nx, 3), "y": round(ny, 3)},
                        "area_xy": {"x": round(ax, 3), "y": round(ay, 3)},
                    })
                    break

    total_area = sum(p.get("area_m2", 0.0) for p in pairs)
    return pairs, total_area


def write_fire_zones_json(pairs: List[Dict[str, object]], total: float, out_path: Path):
    out_obj = {
        "zones": pairs,
        "total_area_m2": round(total, 3),
        "count": len(pairs),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")


def print_fire_zone_texts(csv_path: Path, target_layer: str):
    if not csv_path.exists():
        print(f"未找到CSV：{csv_path}")
        return

    # 先基于图层搜集该块的 ModuleId，避免块内文字在不同图层时丢失
    target_mids = collect_target_module_ids(csv_path, target_layer)

    if not target_mids:
        print(f"在图层 `{target_layer}` 未找到任何相关的 ModuleId。")
        return

    print(f"找到 {len(target_mids)} 个相关 ModuleId，开始筛选文本...")

    rows_to_print: List[Dict[str, str]] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = (row.get("ModuleId", "") or "").strip()
            if not mid or mid not in target_mids:
                continue
            if not row_is_text_like(row):
                continue
            text = (row.get("Text", "") or "").strip()
            if not text:
                continue
            rows_to_print.append(row)

    if not rows_to_print:
        print("未找到任何可打印的文本行。")
        return

    # 打印表头与内容（保持原有行为）
    print(",".join(PRINT_FIELDS))
    for row in rows_to_print:
        out: List[str] = []
        for k in PRINT_FIELDS:
            v = row.get(k, "")
            if v is None:
                v = ""
            v = str(v)
            if "," in v or "\n" in v:
                v = '"' + v.replace('"', '""') + '"'
            out.append(v)
        print(",".join(out))

    # 解析并写出 JSON
    pairs, total = pair_zones_and_areas(rows_to_print)
    out_json = OUTPUT_DIR / "fire_zones.json"
    write_fire_zones_json(pairs, total, out_json)
    print(f"已写出 {out_json}，共 {len(pairs)} 个分区，总面积 {round(total, 3)} m²")


if __name__ == "__main__":
    print_fire_zone_texts(INPUT_CSV, TARGET_LAYER) 