import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Iterable

OUTPUT_DIR = Path("outputs")

# ========== 参数（直接修改） ==========
# input_csv_path = OUTPUT_DIR / "cut_auto.csv"
# output_json_path = OUTPUT_DIR / "parking.json"

# 可选：仅保留这些 SubClass（DXF 类型）的点用于计算外接矩形。
# 例如：["LWPOLYLINE", "LINE", "CIRCLE"]。为空则不过滤，使用模块内所有点。
filter_subclasses: List[str] = []

# 可选：仅保留这些 Layer 的点（用于识别车位模块）。为空则不过滤。
filter_layers: List[str] = [
    "CAR",
    "人防区平时车位",
    "非人防主口车道上车位",
    "充电桩",
    "CAR-无障碍",
    "Z-微型车位",
    "人防区无障碍车位",
    "普通微型车位",
    "微型侧停",
]

slot_type_from: str = "Layer"
# ====================================

# 开关：是否启用按图层过滤（行级 / 模块级）
# 行级：仅累积 Layer ∈ filter_layers 的行（可能导致端点不足，默认关闭）
# 模块级：仅输出出现过 filter_layers 中任意图层的模块（默认开启）
enable_row_layer_filter: bool = False
enable_module_layer_filter: bool = True


def _layers_target_set() -> Optional[set]:
    return set(l.strip() for l in filter_layers) if filter_layers else None


def row_passes_filters(row: Dict[str, str]) -> bool:
    ident = (row.get("ModuleId", "") or row.get("GroupId", "")).strip()
    if not ident:
        return False
    if filter_subclasses and row.get("SubClass") not in filter_subclasses:
        return False
    # 坐标有效：允许 X/Y 或 Start/End/Center 任一成对坐标
    def _has_pair(a: str, b: str) -> bool:
        try:
            ax = row.get(a, ""); by = row.get(b, "")
            if ax != "" and by != "":
                float(ax); float(by)
                return True
        except Exception:
            return False
        return False
    if _has_pair("X", "Y"):
        return True
    if _has_pair("StartX", "StartY"):
        return True
    if _has_pair("EndX", "EndY"):
        return True
    if _has_pair("CenterX", "CenterY"):
        return True
    return False


def collect_allowed_module_ids(csv_path: Path, target_layers: List[str]) -> set:
    layers_set = set([l.strip() for l in target_layers]) if target_layers else None
    allowed = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            layer = (row.get("Layer", "") or "").strip()
            if layers_set is not None and layer not in layers_set:
                continue
            mid = (row.get("ModuleId", "") or "").strip()
            if mid:
                allowed.add(mid)
    return allowed


def accumulate_by_module(csv_path: Path, allowed_module_ids: Optional[set] = None) -> Dict[str, Dict[str, Any]]:
    modules: Dict[str, Dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row_passes_filters(row):
                continue
            layer = (row.get("Layer", "") or "").strip()
            mid_raw = (row.get("ModuleId", "") or row.get("GroupId", "")).strip()
            if not mid_raw:
                continue
            if allowed_module_ids is not None:
                mod_id_for_check = (row.get("ModuleId", "") or "").strip()
                if mod_id_for_check and (mod_id_for_check not in allowed_module_ids):
                    continue
            mid = mid_raw
            try:
                x = float(row["X"]); y = float(row["Y"]) 
            except Exception:
                # 即便 X,Y 无法解析，仍可能存在线段的 Start/End 端点
                x = None; y = None
            entry = modules.setdefault(mid, {
                "points": [],
                "endpoints": [],  # 仅记录真实端点（LINE 的起止点、LWPOLYLINE 顶点、POLYLINE 顶点）
                "lw_endpoints": [],  # 专门记录 LWPOLYLINE 顶点
                "points_by_group": defaultdict(list),
                "group_info": {},  # gid -> dict(closed:bool, hatch_types:set, subclass_counts:Counter)
                "layers": Counter(),
                "subclasses": Counter(),
            })
            # 原始点集（保留以便计算整体包络和统计）
            if x is not None and y is not None:
                entry["points"].append((x, y))
            # 端点收集逻辑
            subclass = (row.get("SubClass", "") or "").strip().upper()
            if subclass in ("LWPOLYLINE", "POLYLINE"):
                if x is not None and y is not None:
                    entry["endpoints"].append((x, y))
                    if subclass == "LWPOLYLINE":
                        entry["lw_endpoints"].append((x, y))
            elif subclass == "LINE":
                # 优先使用 StartX/EndX 字段
                try:
                    sx = row.get("StartX", ""); sy = row.get("StartY", "")
                    ex = row.get("EndX", ""); ey = row.get("EndY", "")
                    if sx != "" and sy != "":
                        entry["endpoints"].append((float(sx), float(sy)))
                    if ex != "" and ey != "":
                        entry["endpoints"].append((float(ex), float(ey)))
                except Exception:
                    if x is not None and y is not None:
                        entry["endpoints"].append((x, y))
            elif subclass == "HATCH":
                # HATCH 路径顶点作为端点（为避免外包矩形被铺装等扩大，这里不再加入 endpoints）
                try:
                    vix = row.get("VertexIndex", "")
                    if str(vix).strip() != "":
                        # 仅用于分组信息与可视化重建，跳过 endpoints 收集
                        pass
                except Exception:
                    pass
            # group 与统计
            gid = (row.get("GroupId", "") or "").strip()
            if gid:
                if x is not None and y is not None:
                    entry["points_by_group"][gid].append((x, y))
                info = entry["group_info"].setdefault(gid, {
                    "closed": False,
                    "hatch_types": set(),
                    "subclass_counts": Counter(),
                })
                is_closed = str(row.get("IsClosed", "")).strip()
                if is_closed in ("1", "True", "true"):
                    info["closed"] = True
                htype = (row.get("HatchPathType", "") or "").strip().upper()
                if htype:
                    info["hatch_types"].add(htype)
                info["subclass_counts"][row.get("SubClass", "")] += 1
            entry["layers"][layer] += 1
            entry["subclasses"][row.get("SubClass", "")] += 1
    for mid, entry in modules.items():
        entry["orig_layer"] = (entry["layers"].most_common(1)[0][0] if entry["layers"] else "")
    return modules


def _best_group_bbox(entry: Dict[str, Any]) -> Tuple[float, float, float, float]:
    def rect(points: List[Tuple[float, float]]):
        return rect_from_points(points)
    candidates: List[Tuple[str, str, float, float, float, List[Tuple[float, float]]]] = []
    for gid, pts in entry.get("points_by_group", {}).items():
        if len(pts) < 4:
            continue
        cx, cy, w, h = rect(pts)
        length = max(w, h)
        short = min(w, h)
        area = float(w) * float(h)
        flags = entry.get("group_info", {}).get(gid, {})
        hatch_types = flags.get("hatch_types", set())
        is_external = ("EXTERNAL" in hatch_types)
        is_closed = bool(flags.get("closed", False))
        # 记录：优先级标签、gid、area 等
        pref = "0"
        if is_external:
            pref = "3"  # 最高
        elif is_closed:
            pref = "2"
        else:
            pref = "1"
        # 尺寸期望匹配标记
        in_expected = (4000 <= int(round(length)) <= 7000 and 2000 <= int(round(short)) <= 4000)
        tag = "Y" if in_expected else "N"
        candidates.append((pref+tag, gid, area, length, short, pts))
    # 优先：pref 高、尺寸命中、面积大
    candidates.sort(key=lambda t: (t[0], t[2]), reverse=True)
    if candidates:
        _, _, _, _, _, pts = candidates[0]
        return rect(pts)
    # 回退：所有组里面积最大
    best_pts = None; best_area = -1.0
    for gid, pts in entry.get("points_by_group", {}).items():
        cx, cy, w, h = rect(pts)
        a = float(w) * float(h)
        if a > best_area:
            best_area = a
            best_pts = pts
    if best_pts is not None:
        return rect(best_pts)
    # 再回退：全部点
    return rect(entry.get("points", []))


def rect_from_points(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    width = max_x - min_x
    height = max_y - min_y
    return cx, cy, width, height


# removed oriented bbox for simplicity per user request


def _select_four_endpoint_corners(endpoints: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """从端点列表中选择四个代表性的“角”点，保证都来自实际端点。
    采用对角方向的线性极值：
      - 最小 (x+y)  -> 左下
      - 最小 (x-y)  -> 左上
      - 最大 (x-y)  -> 右下
      - 最大 (x+y)  -> 右上
    若出现重复，依次选择次优以保持尽量分散。
    """
    if not endpoints:
        return []
    pts = list(dict.fromkeys(endpoints))  # 去重，保持顺序

    def arg_extreme(values: List[float], prefer_min: bool, used: set) -> Optional[int]:
        idxs = sorted(range(len(values)), key=lambda i: values[i], reverse=not prefer_min)
        for i in idxs:
            if i not in used:
                return i
        return None

    xs_plus_ys = [px + py for (px, py) in pts]
    xs_minus_ys = [px - py for (px, py) in pts]

    used_idx = set()
    corners_idx: List[int] = []

    for prefer_min, arr in [(True, xs_plus_ys), (True, xs_minus_ys), (False, xs_minus_ys), (False, xs_plus_ys)]:
        idx = arg_extreme(arr, prefer_min, used_idx)
        if idx is not None:
            used_idx.add(idx)
            corners_idx.append(idx)

    # 如果不足四个，补齐与已选点最远的端点以尽量分散
    def squared_dist(p: Tuple[float, float], q: Tuple[float, float]) -> float:
        dx = p[0] - q[0]; dy = p[1] - q[1]
        return dx*dx + dy*dy

    while len(corners_idx) < 4 and len(corners_idx) < len(pts):
        # 选择与现有角点集合的最小距离最大的点
        best_i = None; best_score = -1.0
        for i in range(len(pts)):
            if i in used_idx: continue
            if not corners_idx:
                score = 0.0
            else:
                score = min(squared_dist(pts[i], pts[j]) for j in corners_idx)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is None:
            break
        used_idx.add(best_i)
        corners_idx.append(best_i)

    return [pts[i] for i in corners_idx[:4]]


def _outer_bbox_with_corners(entry: Dict[str, Any]) -> Tuple[float, float, float, float, List[Tuple[float, float]]]:
    """优先使用按组评估的包络（结合 HATCH 外边界、闭合多段线等），
    若不可用则回退到使用全部点的轴对齐外接矩形。
    返回：cx, cy, width, height, corners(左上、左下、右上、右下)
    """
    # 最高优先：仅使用 LWPOLYLINE 顶点来确定角点（真实顶点）
    try:
        # 优先：使用所有收集到的端点（包含 LWPOLYLINE / POLYLINE 顶点及 LINE 起止点）
        ep_pts: List[Tuple[float, float]] = entry.get("endpoints", []) or []
        if len(ep_pts) >= 4:
            xs = [p[0] for p in ep_pts]
            ys = [p[1] for p in ep_pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            cx = (min_x + max_x) / 2.0
            cy = (min_y + max_y) / 2.0
            width = max_x - min_x
            height = max_y - min_y
            corners = _select_four_endpoint_corners(ep_pts)
            if len(corners) >= 4:
                return cx, cy, width, height, corners[:4]
        # 次优先：仅使用 LWPOLYLINE 顶点
        # lw_pts: List[Tuple[float, float]] = entry.get("lw_endpoints", []) or []
        # if len(lw_pts) >= 4:
        #     xs = [p[0] for p in lw_pts]
        #     ys = [p[1] for p in lw_pts]
        #     min_x, max_x = min(xs), max(xs)
        #     min_y, max_y = min(ys), max(ys)
        #     cx = (min_x + max_x) / 2.0
        #     cy = (min_y + max_y) / 2.0
        #     width = max_x - min_x
        #     height = max_y - min_y
        #     corners = _select_four_endpoint_corners(lw_pts)
        #     if len(corners) >= 4:
        #         return cx, cy, width, height, corners[:4]
    except Exception:
        pass
    try:
        cx, cy, width, height = _best_group_bbox(entry)
    except Exception:
        cx = cy = width = height = 0.0

    def corners_from_bbox(cx: float, cy: float, w: float, h: float) -> List[Tuple[float, float]]:
        if w <= 0.0 or h <= 0.0:
            return []
        min_x = cx - w / 2.0
        max_x = cx + w / 2.0
        min_y = cy - h / 2.0
        max_y = cy + h / 2.0
        return [
            (min_x, max_y),  # 左上
            (min_x, min_y),  # 左下
            (max_x, max_y),  # 右上
            (max_x, min_y),  # 右下
        ]

    if width > 0.0 and height > 0.0:
        return cx, cy, width, height, corners_from_bbox(cx, cy, width, height)

    # 回退：使用全部点
    pts: List[Tuple[float, float]] = entry.get("points", [])
    if not pts:
        return 0.0, 0.0, 0.0, 0.0, []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    width = max_x - min_x
    height = max_y - min_y
    corners = [
        (min_x, max_y),
        (min_x, min_y),
        (max_x, max_y),
        (max_x, min_y),
    ]
    return cx, cy, width, height, corners


def choose_type(entry: Dict[str, Any]) -> str:
    if slot_type_from.lower() == "subclass":
        return entry["subclasses"].most_common(1)[0][0] if entry["subclasses"] else ""
    return entry["layers"].most_common(1)[0][0] if entry["layers"] else ""


def classify_slot_type_by_size(width: float, height: float) -> Optional[str]:
    """基于外接矩形尺寸判断车位类型：
    - 4300x2200 => 微型车位
    - 1935*4674 => 小型车位
    - 5500x2400 => 充电车位
    - 6000x3700 => 无障碍车位
    将长边与短边四舍五入到整数后进行精确匹配。
    """
    # 将宽高标准化为较大为 length, 较小为 width
    length = max(width, height)
    short = min(width, height)
    # 四舍五入到整数
    length_r = int(round(length))
    short_r = int(round(short))
    # 精确匹配
    if length_r == 4300 and short_r == 2200:
        return "微型车位"
    if length_r >= 4670 and length_r <= 4680 and short_r >= 1930 and short_r <= 1940:
        return "小型车位"
    if length_r == 5500 and short_r == 2400:
        return "充电车位"
    if length_r == 6000 and short_r == 3700:
        return "无障碍车位"
    return None


def _outer_bbox_with_corners_allpoints(entry: Dict[str, Any]) -> Tuple[float, float, float, float, List[Tuple[float, float]]]:
    pts: List[Tuple[float, float]] = entry.get("points", [])
    if not pts:
        return 0.0, 0.0, 0.0, 0.0, []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    width = max_x - min_x
    height = max_y - min_y
    # 角点顺序：左上、左下、右上、右下（坐标系Y轴向上假定）
    corners = [
        (min_x, max_y),
        (min_x, min_y),
        (max_x, max_y),
        (max_x, min_y),
    ]
    return cx, cy, width, height, corners


def build_slots_dual(modules: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, Any]], Dict[str, int]]:
    """返回（标准车位列表、标准计数、可疑矩形列表、可疑计数）"""
    final_slots: List[Dict[str, Any]] = []
    final_counts: Dict[str, int] = defaultdict(int)
    suspect_slots: List[Dict[str, Any]] = []
    suspect_counts: Dict[str, int] = defaultdict(int)

    target_layers = set([l.strip() for l in filter_layers]) if filter_layers else None

    for mid, entry in modules.items():
        pts: List[Tuple[float, float]] = entry["points"]
        if len(pts) < 4:
            continue
        if target_layers is not None:
            entry_layers = set([ln.strip() for ln in entry["layers"].keys()])
            if entry_layers.isdisjoint(target_layers):
                continue

        # 使用更可靠的组/LW顶点逻辑计算外包矩形
        cx, cy, w, h, corners = _outer_bbox_with_corners(entry)
        size_type = classify_slot_type_by_size(w, h)
        if size_type:
            orig_layer = entry.get("orig_layer") or ""
            final_counts[size_type] += 1
            final_slots.append({
                "id": mid,
                "x": round(cx, 3),
                "y": round(cy, 3),
                "width": round(w, 3),
                "height": round(h, 3),
                "layer": orig_layer,
                "slot_type": size_type,
                "corners": [{"x": round(px, 3), "y": round(py, 3)} for (px, py) in corners],
            })
        else:
            orig_layer = entry.get("orig_layer") or ""
            suspect_counts[orig_layer] += 1
            suspect_slots.append({
                "id": mid,
                "x": round(cx, 3),
                "y": round(cy, 3),
                "width": round(w, 3),
                "height": round(h, 3),
                "layer": orig_layer,
                "slot_type": None,
                "corners": [{"x": round(px, 3), "y": round(py, 3)} for (px, py) in corners],
            })

    return final_slots, dict(final_counts), suspect_slots, dict(suspect_counts)


def _check_llm_candidates(category_key: str, layers: List[str], output_dir: Path):
    """读取 LLM 解析结果并校验 filter_layers 是否包含在 candidates 中。"""
    llm_path = output_dir / "dxf_layers_analysis.json"
    try:
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        candidates = data.get("parsed", {}).get(category_key, {}).get("candidates", [])
        cand_set = set(candidates)
        missing = [l for l in layers if l not in cand_set]
        if missing:
            print(f"LLM分析文件缺失或错误，请检查dxf_layers_analysis.json文件")
        else:
            print(f"找到LLM分析文件，使用LLM提取的图层")
            global filter_layers
            filter_layers = list(candidates)
    except FileNotFoundError:
        print(f"未找到 LLM 分析文件：{llm_path}")
    except Exception as e:
        
        print(f"读取 LLM 分析文件出错：{e}")


def _rebuild_car_geometry(csv_path: Path, output_dxf: Path, allowed_module_ids: Optional[set] = None):
    """从 CSV 重建 CAR 图层（INSERT 展开后的几何）的可视化 DXF。
    仅使用我们导出的信息重绘：LINE、LWPOLYLINE 的顶点，及可用的 CIRCLE/ARC。
    """
    try:
        import ezdxf
    except ImportError:
        print("缺少依赖：请先 pip install ezdxf")
        return

    # 收集几何
    lines: List[Tuple[float, float, float, float]] = []
    circles: List[Tuple[float, float, float]] = []
    arcs: List[Tuple[float, float, float, float, float]] = []  # cx,cy,r,sa,ea
    lw_groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {} # (mid, gid, typ) -> verts, closed

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = (row.get("ModuleId", "") or "").strip()
            layer = (row.get("Layer", "") or "").strip()
            if allowed_module_ids is not None:
                if not mid or mid not in allowed_module_ids:
                    continue

            subclass = (row.get("SubClass", "") or "").strip().upper()

            if subclass == "LINE":
                try:
                    sx = row.get("StartX", ""); sy = row.get("StartY", "")
                    ex = row.get("EndX", ""); ey = row.get("EndY", "")
                    if sx != "" and sy != "" and ex != "" and ey != "":
                        lines.append((float(sx), float(sy), float(ex), float(ey)))
                except Exception:
                    pass
                continue

            if subclass in ("LWPOLYLINE", "POLYLINE"):
                gid = (row.get("GroupId", "") or "").strip()
                if not gid:
                    continue
                try:
                    x = float(row.get("X", "")); y = float(row.get("Y", ""))
                except Exception:
                    continue
                try:
                    vidx_raw = row.get("VertexIndex", "")
                    has_vidx = str(vidx_raw).strip() != ""
                    vidx = int(vidx_raw) if has_vidx else None
                except Exception:
                    vidx = None
                is_closed_flag = str(row.get("IsClosed", "")).strip() in ("1", "True", "true")
                key = (mid, gid, "LW")
                g = lw_groups.setdefault(key, {"verts": [], "closed": False, "next": 0})
                if vidx is None:
                    vidx = g["next"]
                    g["next"] += 1
                g["verts"].append((vidx, x, y))
                g["closed"] = g["closed"] or is_closed_flag
                continue

            if subclass == "HATCH":
                # 使用 HATCH 路径的顶点（具有 VertexIndex）按 GroupId 串接
                gid = (row.get("GroupId", "") or "").strip()
                try:
                    vix = row.get("VertexIndex", "")
                    has_v = str(vix).strip() != ""
                    x = float(row.get("X", "")); y = float(row.get("Y", ""))
                except Exception:
                    has_v = False
                if not gid or not has_v:
                    continue
                try:
                    vidx = int(vix)
                except Exception:
                    continue
                key = (mid, gid, "H")
                g = lw_groups.setdefault(key, {"verts": [], "closed": True})
                g["verts"].append((vidx, x, y))
                continue

            if subclass == "CIRCLE":
                try:
                    cx = float(row.get("CenterX", "")); cy = float(row.get("CenterY", ""))
                    r = float(row.get("Radius", ""))
                    circles.append((cx, cy, r))
                except Exception:
                    pass
                continue

            if subclass == "ARC":
                try:
                    cx = float(row.get("CenterX", "")); cy = float(row.get("CenterY", ""))
                    r = float(row.get("Radius", ""))
                    sa = float(row.get("StartAngle", "")); ea = float(row.get("EndAngle", ""))
                    arcs.append((cx, cy, r, sa, ea))
                except Exception:
                    pass
                continue

    # 创建 DXF 并绘制
    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()
    try:
        if "REBUILT_CAR" not in doc.layers:
            doc.layers.new(name="REBUILT_CAR")
        doc.layers.get("REBUILT_CAR").dxf.color = 2  # 黄色
    except Exception:
        pass

    # 画 LINE
    for sx, sy, ex, ey in lines:
        try:
            msp.add_line((sx, sy), (ex, ey), dxfattribs={"layer": "REBUILT_CAR"})
        except Exception:
            pass

    # 画 LWPOLYLINE 与 HATCH 边界（按组排序顶点）
    for (_, _, typ), grp in lw_groups.items():
        verts = sorted(grp.get("verts", []), key=lambda t: t[0])
        pts = [(x, y) for (_, x, y) in verts]
        if not pts:
            continue
        closed = bool(grp.get("closed", False))
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        try:
            msp.add_lwpolyline(pts, format="xy", dxfattribs={"layer": "REBUILT_CAR", "closed": closed})
        except Exception:
            for i in range(1, len(pts)):
                try:
                    msp.add_line(pts[i-1], pts[i], dxfattribs={"layer": "REBUILT_CAR"})
                except Exception:
                    pass

    # 画 CIRCLE / ARC
    for cx, cy, r in circles:
        try:
            msp.add_circle((cx, cy), r, dxfattribs={"layer": "REBUILT_CAR"})
        except Exception:
            pass
    for cx, cy, r, sa, ea in arcs:
        try:
            msp.add_arc((cx, cy), r, sa, ea, dxfattribs={"layer": "REBUILT_CAR"})
        except Exception:
            pass

    output_dxf.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(output_dxf))
    print(f"已输出 CAR 重建：{output_dxf}")


# ========== 新的严格按 ModuleId 提取（对齐 inspect_mids_1_2.py） ==========

def _rect_bbox(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float, List[Tuple[float, float]]]:
    if not points:
        return 0.0, 0.0, 0.0, 0.0, []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    w = max_x - min_x
    h = max_y - min_y
    corners = [
        (min_x, max_y),
        (min_x, min_y),
        (max_x, max_y),
        (max_x, min_y),
    ]
    return cx, cy, w, h, corners


def accumulate_modules_strict(csv_path: Path) -> Dict[str, Dict[str, Any]]:
    """严格按 ModuleId 聚合，端点收集方式与 inspect_mids_1_2.py 一致。
    - LWPOLYLINE/POLYLINE: 使用 X,Y 顶点
    - LINE: 使用 Start/End 坐标，无法解析再回退 X,Y
    - 不将 HATCH 顶点混入端点
    - 记录图层/子类统计与 lw 顶点集合（可选）
    - 可选：行级按 filter_layers 过滤（可能减少端点，默认关闭）
    """
    target_layers = _layers_target_set() if enable_row_layer_filter else None
    modules: Dict[str, Dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = (row.get("ModuleId", "") or "").strip()
            if not mid:
                continue
            layer = (row.get("Layer", "") or "").strip()
            if target_layers is not None and layer not in target_layers:
                continue
            entry = modules.setdefault(mid, {
                "layers": Counter(),
                "subclasses": Counter(),
                "ep_all": [],           # 端点集合（用于 bbox）
                "lw_pts": [],           # 仅 LWPOLYLINE 顶点（可选）
            })
            subclass = (row.get("SubClass", "") or "").strip().upper()
            entry["layers"][layer] += 1
            entry["subclasses"][subclass] += 1

            # 端点收集逻辑（严格对齐 inspect 脚本）
            if subclass in ("LWPOLYLINE", "POLYLINE"):
                try:
                    x = float(row.get("X", "")); y = float(row.get("Y", ""))
                    entry["ep_all"].append((x, y))
                    if subclass == "LWPOLYLINE":
                        entry["lw_pts"].append((x, y))
                except Exception:
                    pass
                continue
            if subclass == "LINE":
                try:
                    sx = row.get("StartX", ""); sy = row.get("StartY", "")
                    ex = row.get("EndX", ""); ey = row.get("EndY", "")
                    if sx != "" and sy != "":
                        entry["ep_all"].append((float(sx), float(sy)))
                    if ex != "" and ey != "":
                        entry["ep_all"].append((float(ex), float(ey)))
                except Exception:
                    try:
                        x = float(row.get("X", "")); y = float(row.get("Y", ""))
                        entry["ep_all"].append((x, y))
                    except Exception:
                        pass
                continue
            # 其它类型不混入端点

    # 标注原始主图层
    for mid, entry in modules.items():
        entry["orig_layer"] = (entry["layers"].most_common(1)[0][0] if entry["layers"] else "")
    return modules


def build_slots_from_ep_bbox(modules: Dict[str, Dict[str, Any]]):
    final_slots: List[Dict[str, Any]] = []
    final_counts: Dict[str, int] = defaultdict(int)
    suspect_slots: List[Dict[str, Any]] = []
    suspect_counts: Dict[str, int] = defaultdict(int)

    target_layers = _layers_target_set() if enable_module_layer_filter else None

    for mid, entry in modules.items():
        if target_layers is not None:
            entry_layers = set((entry.get("layers") or {}).keys())
            if entry_layers.isdisjoint(target_layers):
                continue
        ep: List[Tuple[float, float]] = entry.get("ep_all", [])
        if len(ep) < 4:
            continue
        cx, cy, w, h, corners = _rect_bbox(ep)
        size_type = classify_slot_type_by_size(w, h)
        orig_layer = entry.get("orig_layer") or ""
        rec = {
            "id": mid,
            "x": round(cx, 3),
            "y": round(cy, 3),
            "width": round(w, 3),
            "height": round(h, 3),
            "layer": orig_layer,
            "slot_type": size_type if size_type else None,
            "corners": [{"x": round(px, 3), "y": round(py, 3)} for (px, py) in corners],
        }
        if size_type:
            final_slots.append(rec)
            final_counts[size_type] += 1
        else:
            suspect_slots.append(rec)
            suspect_counts[orig_layer] += 1

    return final_slots, dict(final_counts), suspect_slots, dict(suspect_counts)


# ========== 替换主流程使用新的严格逻辑 ==========

def main(output_dir: Path = None):
    global OUTPUT_DIR
    if output_dir is None:
        output_dir = OUTPUT_DIR
    else:
        OUTPUT_DIR = output_dir
    
    input_csv_path = output_dir / "建筑平面图_人防平面.csv"
    final_json_path = output_dir / "final_park.json"
    suspect_json_path = output_dir / "parking.json"
    
    if not input_csv_path.exists():
        raise FileNotFoundError(f"找不到输入 CSV：{input_csv_path}")

    modules = accumulate_modules_strict(input_csv_path)
    final_slots, final_counts, suspect_slots, suspect_counts = build_slots_from_ep_bbox(modules)

    final_obj = {"spaces": final_slots, "counts": final_counts}
    final_json_path.parent.mkdir(parents=True, exist_ok=True)
    final_json_path.write_text(json.dumps(final_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    suspect_obj = {
        "spaces": suspect_slots + final_slots,
        "counts": suspect_counts,
        "standard_counts": final_counts,
    }
    suspect_json_path.write_text(json.dumps(suspect_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"标准车位数：{len(final_slots)}，已写入 {final_json_path}")
    print("标准车位类型计数：")
    for label in ["微型车位", "小型车位", "充电车位", "无障碍车位"]:
        print(f"  {label}: {final_counts.get(label, 0)}")
    print(f"可疑矩形数：{len(suspect_slots)}，已写入 {suspect_json_path}")


if __name__ == "__main__":
    main() 