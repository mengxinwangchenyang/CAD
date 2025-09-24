import csv
import json
from pathlib import Path
from typing import Dict, List, Any

OUTPUT_DIR = Path("outputs")
INPUT_CSV = OUTPUT_DIR / "建筑平面图_人防平面.csv"
TARGET_LAYER = "平时文字"
OUTPUT_JSON = OUTPUT_DIR / "text_layer_texts.json"

PRINT_FIELDS = [
    "ModuleId", "Layer", "SubClass", "Text", "X", "Y", "Z",
    "EntityHandle", "OwnerHandle", "Space"
]


def collect_module_ids_on_layer(csv_path: Path, layer_name: str) -> set:
    mids = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            layer = (row.get("Layer", "") or "").strip()
            if layer != layer_name:
                continue
            mid = (row.get("ModuleId", "") or "").strip()
            if mid:
                mids.add(mid)
    return mids


def row_is_text_like(row: Dict[str, str]) -> bool:
    sc = (row.get("SubClass", "") or "").strip().upper()
    if sc in ("TEXT", "MTEXT", "INSERT"):
        return True
    return False


def extract_texts(csv_path: Path, target_layer: str) -> List[Dict[str, Any]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"未找到CSV：{csv_path}")

    target_mids = collect_module_ids_on_layer(csv_path, target_layer)
    texts: List[Dict[str, Any]] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            layer = (row.get("Layer", "") or "").strip()
            mid = (row.get("ModuleId", "") or "").strip()
            if not row_is_text_like(row):
                continue
            text = (row.get("Text", "") or "").strip()
            if not text:
                continue

            # 条件：
            # 1) 直接在目标图层上的文本（即使 ModuleId 为空）
            # 2) 或者属于从目标图层收集到的 ModuleId（块内嵌套文本）
            if (layer == target_layer) or (mid and mid in target_mids):
                rec = {k: row.get(k, "") for k in PRINT_FIELDS}
                for k in ("X", "Y", "Z"):
                    try:
                        v = rec.get(k, "")
                        rec[k] = float(v) if v not in ("", None) else ""
                    except Exception:
                        pass
                texts.append(rec)

    return texts


def write_texts_json(records: List[Dict[str, Any]], out_path: Path = OUTPUT_JSON, source_csv: Path = INPUT_CSV):
    out = {
        "schema_version": 1,
        "source_csv": str(source_csv),
        "layer": TARGET_LAYER,
        "count": len(records),
        "texts": records,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    records = extract_texts(INPUT_CSV, TARGET_LAYER)
    write_texts_json(records, OUTPUT_JSON, INPUT_CSV)
    print(f"已输出 {OUTPUT_JSON}，记录数：{len(records)}")


if __name__ == "__main__":
    main() 