import json
import csv
from pathlib import Path
from typing import Any, Dict, List

# ========= 参数（直接修改） =========
OUTPUT_DIR = Path("outputs")
INPUT_DXF = Path("full.dxf")  # 若不存在，将自动回退到同目录下的“建筑平面图_人防平面.dxf”
PARKING_JSON = OUTPUT_DIR / "final_park.json"
OUTPUT_DXF = OUTPUT_DIR / "parking_debug_visualized.dxf"

# 标注样式
MARKER_SIZE = 100.0
TEXT_HEIGHT = 150.0
TEXT_OFFSET_ID = (100.0, 100.0)      # ID 文本偏移
TEXT_OFFSET_TYPE = (100.0, 100.0+170.0)  # 车型在 ID 上方
TEXT_HEIGHT_CSV_MID = 120.0
CSV_LABEL_OFFSET = (0.0, 0.0)
TEXT_HEIGHT_CSV_MID_PER_ROW = 90.0
MID_LABEL_LAYER = "MID_DEBUG"
# 颜色（ACI）
COLOR_BLUE = 5
COLOR_WHITE = 7
COLOR_CYAN = 4
COLOR_YELLOW = 2
# ==================================


def ensure_layer(doc, layer_name: str, color: int = None):
    layers = doc.layers
    if layer_name not in layers:
        layers.new(name=layer_name)
    if color is not None:
        try:
            layers.get(layer_name).dxf.color = color
        except Exception:
            pass


def ensure_default_material(doc):
    """确保 materials['ByLayer'] 为有效实体，必要时删除无效占位后重建。"""
    try:
        mat = doc.materials.get("ByLayer")
        if not hasattr(mat, "dxf"):
            # 发现无效条目（如字符串），删除后重建
            try:
                del doc.materials["ByLayer"]
            except Exception:
                pass
            try:
                doc.materials.new("ByLayer")
            except Exception:
                pass
    except Exception:
        try:
            doc.materials.new("ByLayer")
        except Exception:
            pass


def add_cross(msp, x: float, y: float, size: float, layer: str, color: int):
    h = size / 2.0
    msp.add_line((x - h, y), (x + h, y), dxfattribs={"layer": layer, "color": color})
    msp.add_line((x, y - h), (x, y + h), dxfattribs={"layer": layer, "color": color})


def add_circle(msp, x: float, y: float, size: float, layer: str, color: int):
    r = size / 2.0
    msp.add_circle((x, y), r, dxfattribs={"layer": layer, "color": color})


def add_text(msp, text: str, x: float, y: float, layer: str, height: float, dx: float, dy: float, color: int):
    ent = msp.add_text(text, dxfattribs={"layer": layer, "height": height, "color": color})
    try:
        ent.set_pos((x + dx, y + dy))
    except Exception:
        try:
            ent.dxf.insert = (x + dx, y + dy)
        except Exception:
            pass


def add_rect_from_corners(msp, corners: List[Dict[str, float]], layer: str, color: int):
    try:
        pts = [(float(c.get("x")), float(c.get("y"))) for c in corners[:4]]
        if len(pts) >= 4:
            # 闭合
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            msp.add_lwpolyline(pts, format="xy", dxfattribs={"layer": layer, "color": color, "closed": True})
    except Exception:
        pass


def load_parking(json_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "spaces" in data:
        return list(data["spaces"])
    if isinstance(data, list):
        return data
    return []


def load_module_centers_from_csv(csv_path: Path) -> Dict[str, tuple]:
    centers: Dict[str, tuple] = {}
    sums: Dict[str, List[float]] = {}
    counts: Dict[str, int] = {}
    if not csv_path.exists():
        return centers
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = (row.get("ModuleId", "") or "").strip()
            if not mid:
                continue
            # 取一个代表点：优先 X,Y；否则 Start/End/Center
            x = row.get("X", ""); y = row.get("Y", "")
            px = py = None
            try:
                if x != "" and y != "":
                    px = float(x); py = float(y)
            except Exception:
                px = py = None
            if px is None or py is None:
                for pair in (("StartX", "StartY"), ("EndX", "EndY"), ("CenterX", "CenterY")):
                    try:
                        sx = row.get(pair[0], ""); sy = row.get(pair[1], "")
                        if sx != "" and sy != "":
                            px = float(sx); py = float(sy); break
                    except Exception:
                        pass
            if px is None or py is None:
                continue
            s = sums.setdefault(mid, [0.0, 0.0])
            s[0] += px; s[1] += py
            counts[mid] = counts.get(mid, 0) + 1
    for mid, s in sums.items():
        c = max(1, counts.get(mid, 1))
        centers[mid] = (s[0] / c, s[1] / c)
    return centers


def _row_point(row: Dict[str, str]):
    # 返回 (x,y) 或 None
    for pair in (("X", "Y"), ("StartX", "StartY"), ("EndX", "EndY"), ("CenterX", "CenterY")):
        try:
            sx = row.get(pair[0], ""); sy = row.get(pair[1], "")
            if sx != "" and sy != "":
                return float(sx), float(sy)
        except Exception:
            pass
    return None


def label_all_rows_module_ids_from_csv(msp, csv_path: Path, layer_name: str, text_height: float):
    if not csv_path.exists():
        return 0
    n = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = (row.get("ModuleId", "") or "").strip()
            if not mid:
                continue
            pt = _row_point(row)
            if pt is None:
                continue
            x, y = pt
            add_text(msp, f"MID:{mid}", float(x), float(y), layer_name, text_height, 0.0, 0.0, COLOR_YELLOW)
            n += 1
    return n


def main(output_dir: Path = None):
    global OUTPUT_DIR
    if output_dir is None:
        output_dir = OUTPUT_DIR
    else:
        OUTPUT_DIR = output_dir

    try:
        import ezdxf
        from ezdxf.addons import Importer
    except ImportError:
        raise SystemExit("缺少依赖：请先 pip install ezdxf")

    # 选择 DXF：优先 full.dxf，否则回退到“建筑平面图_人防平面.dxf”
    input_dxf = INPUT_DXF
    if not input_dxf.exists():
        fallback = Path("建筑平面图_人防平面.dxf")
        if fallback.exists():
            input_dxf = fallback

    if not input_dxf.exists():
        raise FileNotFoundError(f"找不到 DXF：{input_dxf}")
    if not PARKING_JSON.exists():
        raise FileNotFoundError(f"找不到停车位 JSON：{PARKING_JSON}")

    # 读原始 DXF（可能带有损坏的 materials 表）
    src_doc = ezdxf.readfile(str(input_dxf))

    # 在全新的 DXF 文档中进行可视化，避免源文件资源表问题
    dst_doc = ezdxf.new(setup=True)
    importer = Importer(src_doc, dst_doc)
    importer.import_modelspace()
    importer.finalize()

    msp = dst_doc.modelspace()

    # 图层
    debug_layer = "PARKING_DEBUG"
    ensure_layer(dst_doc, debug_layer, COLOR_CYAN)
    ensure_layer(dst_doc, MID_LABEL_LAYER, COLOR_YELLOW)

    # 读停车位
    spaces = load_parking(PARKING_JSON)
    print(f"加载停车位 {len(spaces)} 个")

    # 逐个绘制：十字+圆+外包矩形（若有），标注序号（从1开始）、车型
    labeled_ids = set()
    for idx, rec in enumerate(spaces, start=1):
        x = float(rec.get("x", 0.0))
        y = float(rec.get("y", 0.0))
        add_cross(msp, x, y, MARKER_SIZE, debug_layer, COLOR_CYAN)
        add_circle(msp, x, y, MARKER_SIZE, debug_layer, COLOR_CYAN)
        # 画外包矩形（如果 extract 写入了 corners）
        corners = rec.get("corners")
        if isinstance(corners, list) and len(corners) >= 4:
            add_rect_from_corners(msp, corners, debug_layer, COLOR_CYAN)
        # 用 ModuleId 标注（同一 id 只标一次）
        mid = str(rec.get("id", idx))
        if mid not in labeled_ids:
            labeled_ids.add(mid)
            add_text(msp, f"MID:{mid}", x, y, debug_layer, TEXT_HEIGHT, TEXT_OFFSET_ID[0], TEXT_OFFSET_ID[1], COLOR_WHITE)
        slot_type = rec.get("slot_type") or rec.get("layer") or "小型车"
        add_text(msp, str(slot_type), x, y, debug_layer, TEXT_HEIGHT*0.8, TEXT_OFFSET_TYPE[0], TEXT_OFFSET_TYPE[1], COLOR_WHITE)

    # # 追加：从完整 CSV 标注所有出现过的 ModuleId（每个仅一次）
    # csv_path = output_dir / "建筑平面图_人防平面.csv"
    # csv_centers = load_module_centers_from_csv(csv_path)
    # print(f"CSV 模块数：{len(csv_centers)}")
    # for mid, (cx, cy) in csv_centers.items():
    #     if mid in labeled_ids:
    #         continue
    #     labeled_ids.add(mid)
    #     add_text(msp, f"MID:{mid}", float(cx), float(cy), MID_LABEL_LAYER, TEXT_HEIGHT_CSV_MID, CSV_LABEL_OFFSET[0], CSV_LABEL_OFFSET[1], COLOR_YELLOW)

    # # 再追加：为 CSV 中的每一行在其点位都标注一次 MID（允许重复）
    # total_row_labels = label_all_rows_module_ids_from_csv(msp, csv_path, MID_LABEL_LAYER, TEXT_HEIGHT_CSV_MID_PER_ROW)
    # print(f"逐行 MID 标注数：{total_row_labels}")

    # 保存
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_default_material(dst_doc)
    try:
        dst_doc.saveas(str(OUTPUT_DXF))
        print(f"已输出：{OUTPUT_DXF}")
    except PermissionError:
        try:
            alt_path = OUTPUT_DXF.with_name(OUTPUT_DXF.stem + "_alt" + OUTPUT_DXF.suffix)
            dst_doc.saveas(str(alt_path))
            print(f"目标文件被占用，已改存：{alt_path}")
        except Exception as e:
            raise


if __name__ == "__main__":
    main() 