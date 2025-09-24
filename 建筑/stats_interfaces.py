import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

OUTPUT_DIR = Path("outputs")
FINAL_PARK_JSON = OUTPUT_DIR / "final_park.json"
FIRE_ZONES_JSON = OUTPUT_DIR / "fire_zones.json"
TEXT_LAYER_JSON = OUTPUT_DIR / "text_layer_texts.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"找不到文件: {path}")
    except Exception as e:
        raise RuntimeError(f"读取 {path} 失败: {e}")


def get_slot_counts(final_park_path: Path = FINAL_PARK_JSON) -> Dict[str, Any]:
    """统计车位数量：返回各 slot_type 的数量以及总数。
    期望 final_park.json 结构: {"spaces": [{"slot_type": str, ...}, ...]}
    """
    data = _read_json(final_park_path)
    spaces: List[Dict[str, Any]] = data.get("spaces", []) if isinstance(data, dict) else []
    counts: Dict[str, int] = {}
    total = 0
    for sp in spaces:
        st = (sp.get("slot_type") if isinstance(sp, dict) else None) or ""
        if not st:
            st = "未知类型"
        counts[st] = counts.get(st, 0) + 1
        total += 1
    return {"by_type": counts, "total": total}


def get_fire_zone_areas(fire_zones_path: Path = FIRE_ZONES_JSON) -> Dict[str, Any]:
    """统计防火分区面积（单位：平方米）：返回 {name: area_m2, ...} 以及总面积。"""
    data = _read_json(fire_zones_path)
    zones: List[Dict[str, Any]] = data.get("zones", []) if isinstance(data, dict) else []
    areas: Dict[str, float] = {}
    total = 0.0
    for z in zones:
        name = (z.get("name") if isinstance(z, dict) else None) or ""
        area = z.get("area_m2") if isinstance(z, dict) else None
        try:
            area_v = float(area)
        except Exception:
            area_v = 0.0
        if not name:
            name = f"ZONE_{len(areas)+1}"
        areas[name] = areas.get(name, 0.0) + area_v
        total += area_v
    return {"by_zone": areas, "total_area_m2": round(total, 3), "unit": "平方米"}


def get_slot_dimensions() -> Dict[str, Any]:
    """返回每种车位的硬编码尺寸，单位：毫米。
      - 微型车位: 4300 x 2200
      - 小型车位: 5500 x 2400
      - 充电车位: 5500 x 2400
      - 无障碍车位: 6000 x 3700"""
    sizes: Dict[str, Tuple[int, int]] = {
        "微型车位": (4300, 2200),
        "小型车位": (5500, 2400),
        "充电车位": (5500, 2400),
        "无障碍车位": (6000, 3700),
    }
    return {"unit": "毫米", "sizes": sizes}


def compute_single_car_area(final_park_path: Path = FINAL_PARK_JSON, fire_zones_path: Path = FIRE_ZONES_JSON) -> Dict[str, Any]:
    """计算单车停车面积：S = 防火分区总面积 / 车位数量（单位：平方米）。
    返回 {"total_area_m2": float, "total_area_unit": "平方米", "slots_total": int, "single_car_area_m2": float, "single_car_area_unit": "平方米"}"""
    slot_info = get_slot_counts(final_park_path)
    fire_info = get_fire_zone_areas(fire_zones_path)
    total_area = float(fire_info.get("total_area_m2", 0.0))
    slots_total = int(slot_info.get("total", 0))
    single = (total_area / slots_total) if slots_total > 0 else 0.0
    return {
        "total_area_m2": round(total_area, 3),
        "total_area_unit": "平方米",
        "slots_total": slots_total,
        "single_car_area_m2": round(single, 3),
        "single_car_area_unit": "平方米",
    }


def get_text_layer_texts(texts_json_path: Path = TEXT_LAYER_JSON) -> Dict[str, Any]:
    """返回 TEXT 图层提取到的文本信息（读取 outputs/text_layer_texts.json）。"""
    data = _read_json(texts_json_path)
    # 直接返回独立小对象：count、texts里面的Text属性
    return {
        "count": data.get("count", 0),
        "texts": [t.get("Text", "") for t in data.get("texts", [])],
    }


if __name__ == "__main__":
    # 简单演示
    try:
        slot_counts = get_slot_counts()
        print("车位统计:", json.dumps(slot_counts, ensure_ascii=False))
    except Exception as e:
        print("车位统计失败:", e)
    try:
        fire_areas = get_fire_zone_areas()
        print("防火分区面积:", json.dumps(fire_areas, ensure_ascii=False))
    except Exception as e:
        print("防火分区统计失败:", e)
    try:
        single = compute_single_car_area()
        print("单车停车面积:", json.dumps(single, ensure_ascii=False))
    except Exception as e:
        print("单车停车面积计算失败:", e)
    try:
        texts_info = get_text_layer_texts()
        print("TEXT图层文本:", json.dumps({"count": texts_info.get("count"), "texts": texts_info.get("texts")}, ensure_ascii=False))
    except Exception as e:
        print("读取TEXT图层文本失败:", e) 