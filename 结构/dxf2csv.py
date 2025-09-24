import argparse
import csv
import sys
from pathlib import Path
from typing import Optional
from ezdxf.math import OCS

try:
    import ezdxf
except ImportError:
    print("缺少依赖：请先运行 pip install ezdxf", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = Path("outputs")

def safe_get(dxf, name, default=""):
    try:
        return getattr(dxf, name)
    except AttributeError:
        return default


def point_to_xyz(p):
    """将 ezdxf 的点对象/元组统一成 (x,y,z) 浮点三元组。"""
    if p is None:
        return (None, None, None)
    # ezdxf VEC3 既可索引也可 .x .y .z
    try:
        return (float(p[0]), float(p[1]), float(getattr(p, "z", 0.0)))
    except Exception:
        try:
            return (float(p.x), float(p.y), float(getattr(p, "z", 0.0)))
        except Exception:
            return (None, None, None)


def to_wcs_xyz(e, x: float, y: float, z: float = 0.0):
    """Convert (x,y,z) from entity OCS to WCS using its extrusion if present, then round to 3 decimals."""
    try:
        extr = getattr(e.dxf, "extrusion", None)
        if extr is not None:
            X, Y, Z = OCS(extr).to_wcs((x, y, z))
            return round(float(X), 3), round(float(Y), 3), round(float(Z), 3)
    except Exception:
        pass
    return round(float(x), 3), round(float(y), 3), round(float(z), 3)


def entity_common_fields(e):
    layer = safe_get(e.dxf, "layer", "")
    ltype = safe_get(e.dxf, "linetype", "")
    subclass = e.dxftype()  # 作为 SubClass 输出
    return layer, ltype, subclass


def text_content(e):
    # TEXT / ATTRIB: .dxf.text; MTEXT: e.text (组合后的纯文本)
    if e.dxftype() in ("TEXT", "ATTRIB"):
        return safe_get(e.dxf, "text", "")
    if e.dxftype() == "MTEXT":
        try:
            return e.text  # ezdxf 已做拼接
        except Exception:
            return safe_get(e.dxf, "text", "")
    return ""


def _assign_group_numeric_id(ctx, layer: str, raw_key: str) -> int:
    key = f"{layer}|{raw_key}"
    m = ctx["group_id_map"]
    if key not in m:
        m[key] = ctx["next_group_id"]
        ctx["next_group_id"] += 1
    return m[key]


def _assign_module_numeric_id(ctx, layer: str, raw_key: str) -> int:
    # 为了确保 INSERT 及其展开的虚拟实体在不同图层下也保持相同的 ModuleId，
    # 这里仅使用 raw_key 作为键，不再包含 layer。
    key = f"{raw_key}"
    m = ctx["module_id_map"]
    if key not in m:
        m[key] = ctx["next_module_id"]
        ctx["next_module_id"] += 1
    return m[key]


def handle_entity(e, rows, space_name: str, depth=0, group_key: Optional[str] = None, module_key: Optional[str] = None, ctx: Optional[dict] = None, curr_block_id: Optional[int] = None, father_block_id: Optional[int] = None, root_block_id: Optional[int] = None):
    dxftype = e.dxftype()
    layer, ltype, subclass = entity_common_fields(e)

    # helpers: build extended row with defaults
    def make_row():
        # [X,Y,Z,Layer,SubClass,Linetype,Text,GroupId,ModuleId,
        #  EntityHandle,OwnerHandle,VertexIndex,Bulge,StartWidth,EndWidth,
        #  StartX,StartY,StartZ,EndX,EndY,EndZ,
        #  CenterX,CenterY,CenterZ,Radius,StartAngle,EndAngle,IsClosed,
        #  HatchPatternName,HatchPatternScale,HatchPatternAngle,HatchSolid,
        #  HatchPathIndex,HatchPathIsHole,HatchPathType,
        #  Space, ModuleFather, ModuleNowID, ModuleRoot]
        row = ["", "", "", layer, subclass, ltype, "", "", "",
               safe_get(e.dxf, "handle", ""), safe_get(e.dxf, "owner", ""), "", "", "", "",
               "", "", "", "", "", "",
               "", "", "", "", "", "", "",
               "", "", "", "",
               "", "", "",
               space_name, "", "", ""]
        # fill module hierarchy if within a block context
        if curr_block_id is not None:
            row[36] = father_block_id if father_block_id is not None else ""
            row[37] = curr_block_id
            row[38] = root_block_id if root_block_id is not None else curr_block_id
        return row

    def set_xyz(row, x, y, z):
        X, Y, Z = to_wcs_xyz(e, x or 0.0, y or 0.0, z or 0.0)
        row[0], row[1], row[2] = X, Y, Z
        return row

    # POINT
    if dxftype == "POINT":
        x, y, z = point_to_xyz(safe_get(e.dxf, "location", None))
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
        rows.append(r)
        return

    # LINE: output start & end
    if dxftype == "LINE":
        sx, sy, sz = point_to_xyz(safe_get(e.dxf, "start", None))
        ex, ey, ez = point_to_xyz(safe_get(e.dxf, "end", None))
        r = make_row()
        if sx is not None and sy is not None:
            set_xyz(r, sx, sy, sz or 0.0)
        if sx is not None and sy is not None:
            SX, SY, SZ = to_wcs_xyz(e, sx or 0.0, sy or 0.0, sz or 0.0)
            r[15], r[16], r[17] = SX, SY, SZ
        if ex is not None and ey is not None:
            EX, EY, EZ = to_wcs_xyz(e, ex or 0.0, ey or 0.0, ez or 0.0)
            r[18], r[19], r[20] = EX, EY, EZ
        r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        rows.append(r)
        return

    # LWPOLYLINE: per vertex row with extra attributes
    if dxftype == "LWPOLYLINE":
        z0 = float(safe_get(e.dxf, "elevation", 0.0)) or 0.0
        is_closed = getattr(e, "closed", False)
        raw_gkey = group_key or (safe_get(e.dxf, "handle", "") or hex(id(e)))
        gid_num = _assign_group_numeric_id(ctx, layer, raw_gkey) if ctx else raw_gkey
        mod_num = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        try:
            for idx, vx in enumerate(e.vertices()):
                x = float(vx[0]); y = float(vx[1])
                start_w = ""; end_w = ""; bulge = ""
                try:
                    start_w = float(vx[2])
                    end_w = float(vx[3])
                    bulge = float(vx[4])
                except Exception:
                    pass
                r = make_row()
                set_xyz(r, x, y, z0)
                r[7] = str(gid_num)
                r[8] = mod_num
                r[11] = idx
                r[12] = bulge
                r[13] = start_w
                r[14] = end_w
                r[27] = 1 if is_closed else 0
                rows.append(r)
        except Exception:
            pass
        return

    # 2D/3D POLYLINE & VERTEX: keep as points, fill extended columns blank
    if dxftype in ("POLYLINE", "MESH", "POLYFACE"):
        raw_gkey = group_key or (safe_get(e.dxf, "handle", "") or hex(id(e))) if dxftype == "POLYLINE" else (group_key or "")
        gid_num = _assign_group_numeric_id(ctx, layer, raw_gkey) if (ctx and dxftype == "POLYLINE" and raw_gkey) else ""
        mod_num = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        try:
            for v in e.vertices():
                x, y, z = point_to_xyz((safe_get(v.dxf, "location", None) or (safe_get(v.dxf, "x", 0.0),
                                                                              safe_get(v.dxf, "y", 0.0),
                                                                              safe_get(v.dxf, "z", 0.0))))
                if x is None:
                    x = float(safe_get(v.dxf, "x", 0.0))
                    y = float(safe_get(v.dxf, "y", 0.0))
                    z = float(safe_get(v.dxf, "z", 0.0))
                r = make_row()
                set_xyz(r, float(x), float(y), float(z or 0.0))
                r[7] = str(gid_num) if gid_num != "" else ""
                r[8] = mod_num
                rows.append(r)
        except Exception:
            pass
        return

    # FACE3D
    if dxftype == "FACE3D":
        mod_num = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
            x, y, z = point_to_xyz(safe_get(e.dxf, name, None))
            if x is not None:
                r = make_row()
                set_xyz(r, x, y, z or 0.0)
                r[8] = mod_num
                rows.append(r)
        return

    # CIRCLE
    if dxftype == "CIRCLE":
        x, y, z = point_to_xyz(safe_get(e.dxf, "center", None))
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
            CX, CY, CZ = to_wcs_xyz(e, x or 0.0, y or 0.0, z or 0.0)
            r[21], r[22], r[23] = CX, CY, CZ
        r[24] = safe_get(e.dxf, "radius", "")
        r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        rows.append(r)
        return

    # ARC
    if dxftype == "ARC":
        x, y, z = point_to_xyz(safe_get(e.dxf, "center", None))
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
            CX, CY, CZ = to_wcs_xyz(e, x or 0.0, y or 0.0, z or 0.0)
            r[21], r[22], r[23] = CX, CY, CZ
        r[24] = safe_get(e.dxf, "radius", "")
        r[25] = safe_get(e.dxf, "start_angle", "")
        r[26] = safe_get(e.dxf, "end_angle", "")
        r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        rows.append(r)
        return

    # ELLIPSE: center only
    if dxftype == "ELLIPSE":
        x, y, z = point_to_xyz(safe_get(e.dxf, "center", None))
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
            CX, CY, CZ = to_wcs_xyz(e, x or 0.0, y or 0.0, z or 0.0)
            r[21], r[22], r[23] = CX, CY, CZ
        r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        rows.append(r)
        return

    # SPLINE: control points
    if dxftype == "SPLINE":
        mod_num = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        try:
            for p in e.control_points:
                x, y, z = point_to_xyz(p)
                r = make_row()
                if x is not None and y is not None:
                    set_xyz(r, x, y, z or 0.0)
                r[8] = mod_num
                rows.append(r)
        except Exception:
            pass
        return

    # TEXT / MTEXT
    if dxftype in ("TEXT", "MTEXT"):
        base_pt = safe_get(e.dxf, "insert", None) if dxftype == "TEXT" else safe_get(e.dxf, "insert", None)
        if base_pt is None:
            base_pt = safe_get(e.dxf, "align_point", None)
        x, y, z = point_to_xyz(base_pt)
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
        r[6] = text_content(e)
        r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
        rows.append(r)
        return

    # INSERT: insert point + expand
    if dxftype == "INSERT":
        # assign sequential ModuleNowID for every INSERT
        if ctx is not None:
            next_id = ctx.get("next_insert_id", 1)
            ctx["next_insert_id"] = next_id + 1
        else:
            next_id = 1
        parent_id = curr_block_id
        this_root_id = root_block_id if root_block_id is not None else next_id

        x, y, z = point_to_xyz(safe_get(e.dxf, "insert", None))
        txts = []
        try:
            for att in e.attribs():
                t = text_content(att)
                if t:
                    tag = safe_get(att.dxf, "tag", "")
                    txts.append(f"{tag}={t}" if tag else t)
        except Exception:
            pass
        raw_mkey = module_key or (safe_get(e.dxf, "handle", "") or hex(id(e)))
        mod_num = str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey
        # 记录块名称与 ModuleNowID 的映射
        try:
            blk_name = safe_get(e.dxf, "name", "")
            if ctx is not None:
                ctx.setdefault("module_nowid_to_block_name", {})[str(next_id)] = blk_name
        except Exception:
            pass
        r = make_row()
        if x is not None and y is not None:
            set_xyz(r, x, y, z or 0.0)
        r[6] = "; ".join(txts)
        r[8] = mod_num
        # override module hierarchy for the INSERT row itself
        r[36] = parent_id if parent_id is not None else ""
        r[37] = next_id
        r[38] = this_root_id
        rows.append(r)

        if depth < 10:
            try:
                for i, ve in enumerate(e.virtual_entities()):
                    ve_h = safe_get(getattr(ve, "dxf", object()), "handle", "") or hex(id(ve))
                    gkey = f"{raw_mkey}:{ve_h}:{i}"
                    handle_entity(ve, rows, space_name, depth + 1, group_key=gkey, module_key=raw_mkey, ctx=ctx, curr_block_id=next_id, father_block_id=parent_id, root_block_id=this_root_id)
            except Exception:
                pass
        return

    # HATCH: prefer seed points; fallback to per-path rows
    if dxftype == "HATCH":
        try:
            handle = safe_get(e.dxf, "handle", "") or hex(id(e))
            # hatch pattern fields (fallback to pattern object if dxf attrs missing)
            try:
                pname = safe_get(e.dxf, "pattern_name", "") or getattr(getattr(e, "pattern", object()), "name", "")
            except Exception:
                pname = ""
            try:
                pscale = safe_get(e.dxf, "pattern_scale", "")
                if pscale in (None, ""):
                    pscale = getattr(getattr(e, "pattern", object()), "scale", "")
            except Exception:
                pscale = ""
            try:
                pangle = safe_get(e.dxf, "pattern_angle", "")
                if pangle in (None, ""):
                    pangle = getattr(getattr(e, "pattern", object()), "rotation", "")
            except Exception:
                pangle = ""
            solid = safe_get(e.dxf, "solid_fill", "")

            # Try seed points first (one seed ~ one island)
            seeds = []
            try:
                seeds = list(getattr(e, "seeds", []) or [])
            except Exception:
                seeds = []
            if not seeds:
                try:
                    seeds = list(e.get_seed_points())
                except Exception:
                    seeds = []

            if seeds:
                for idx, sp in enumerate(seeds):
                    sx, sy, sz = point_to_xyz(sp)
                    r = make_row()
                    if sx is not None and sy is not None:
                        set_xyz(r, sx, sy, sz or 0.0)
                        CX, CY, CZ = to_wcs_xyz(e, sx or 0.0, sy or 0.0, sz or 0.0)
                        r[21], r[22], r[23] = CX, CY, CZ
                    r[28] = pname
                    r[29] = pscale
                    r[30] = pangle
                    r[31] = solid
                    raw_mkey = f"{handle}:seed{idx}"
                    # 关键：使用父 INSERT 的 module_key 作为 ModuleId
                    r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else (str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey)
                    rows.append(r)
                return

            # Fallback: boundary paths
            paths = list(getattr(e, "paths", []))
            if not paths:
                # ultimate fallback single row when no paths accessible
                r = make_row()
                r[28] = pname
                r[29] = pscale
                r[30] = pangle
                r[31] = solid
                raw_mkey = f"{handle}:p0"
                # 关键：使用父 INSERT 的 module_key 作为 ModuleId
                r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else (str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey)
                rows.append(r)
                return

            for idx, path in enumerate(paths):
                # representative point for this path
                x = y = z = None
                try:
                    edges = getattr(path, "edges", [])
                    if edges and len(edges) > 0:
                        edge0 = edges[0]
                        if hasattr(edge0, "start"):
                            x, y, z = point_to_xyz(edge0.start)
                        elif hasattr(edge0, "center"):
                            x, y, z = point_to_xyz(edge0.center)
                    if (x is None or y is None) and hasattr(path, "vertices"):
                        verts = getattr(path, "vertices", [])
                        if verts:
                            vx, vy, vz = point_to_xyz(verts[0])
                            x, y, z = vx, vy, vz
                except Exception:
                    pass

                r = make_row()
                if x is not None and y is not None:
                    set_xyz(r, x, y, z or 0.0)
                r[28] = pname
                r[29] = pscale
                r[30] = pangle
                r[31] = solid
                r[32] = idx
                try:
                    is_hole = bool(getattr(path, "is_hole", False))
                except Exception:
                    is_hole = False
                r[33] = 1 if is_hole else 0
                try:
                    if getattr(path, "is_external", False) or getattr(path, "is_outermost", False):
                        ptype = "EXTERNAL"
                    else:
                        ptype = "INTERNAL"
                except Exception:
                    ptype = "UNKNOWN"
                r[34] = ptype

                raw_mkey = f"{handle}:p{idx}"
                # 关键：使用父 INSERT 的 module_key 作为 ModuleId
                r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else (str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey)
                # 为该路径分配一个 GroupId，便于按路径重建
                try:
                    gid_num = _assign_group_numeric_id(ctx, layer, raw_mkey)
                    r[7] = str(gid_num)
                except Exception:
                    r[7] = raw_mkey
                rows.append(r)

                # 追加：输出该路径的所有顶点，便于重建边界（尤其是矩形框）
                try:
                    verts = list(getattr(path, "vertices", []) or [])
                except Exception:
                    verts = []
                if verts:
                    for v_idx, v in enumerate(verts):
                        vx, vy, vz = point_to_xyz(v)
                        if vx is None or vy is None:
                            continue
                        rv = make_row()
                        set_xyz(rv, vx, vy, vz or 0.0)
                        rv[11] = v_idx  # VertexIndex
                        rv[28] = pname
                        rv[29] = pscale
                        rv[30] = pangle
                        rv[31] = solid
                        rv[32] = idx
                        rv[33] = 1 if is_hole else 0
                        rv[34] = ptype
                        # 关键：使用父 INSERT 的 module_key 作为 ModuleId
                        rv[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else (str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey)
                        try:
                            gid_num2 = _assign_group_numeric_id(ctx, layer, raw_mkey)
                            rv[7] = str(gid_num2)
                        except Exception:
                            rv[7] = raw_mkey
                        rows.append(rv)
                else:
                    # 顶点列表不可用时，尝试基于 edges 生成伪顶点（按顺序输出）
                    try:
                        edges = list(getattr(path, "edges", []) or [])
                    except Exception:
                        edges = []
                    if edges:
                        vlist = []
                        for eidx, edge in enumerate(edges):
                            try:
                                if hasattr(edge, "start") and hasattr(edge, "end"):
                                    sx, sy, sz = point_to_xyz(edge.start)
                                    ex, ey, ez = point_to_xyz(edge.end)
                                    if sx is not None and sy is not None:
                                        vlist.append((sx, sy, sz or 0.0))
                                    # 仅在最后一条边时追加终点，避免重复
                                    if eidx == len(edges) - 1 and ex is not None and ey is not None:
                                        vlist.append((ex, ey, ez or 0.0))
                                elif hasattr(edge, "center") and hasattr(edge, "radius") and hasattr(edge, "start_angle") and hasattr(edge, "end_angle"):
                                    # 圆弧：用起点作为伪顶点；必要时可加终点
                                    try:
                                        cx, cy, cz = point_to_xyz(edge.center)
                                        # 近似计算起点
                                        import math
                                        sa = float(getattr(edge, "start_angle", 0.0)) * math.pi / 180.0
                                        rads = float(getattr(edge, "radius", 0.0))
                                        sx = cx + rads * math.cos(sa)
                                        sy = cy + rads * math.sin(sa)
                                        vlist.append((sx, sy, cz or 0.0))
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # 写出伪顶点
                        for v_idx, (vx, vy, vz) in enumerate(vlist):
                            rv = make_row()
                            set_xyz(rv, vx, vy, vz or 0.0)
                            rv[11] = v_idx
                            rv[28] = pname
                            rv[29] = pscale
                            rv[30] = pangle
                            rv[31] = solid
                            rv[32] = idx
                            rv[33] = 1 if is_hole else 0
                            rv[34] = ptype
                            rv[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else (str(_assign_module_numeric_id(ctx, layer, raw_mkey)) if ctx else raw_mkey)
                            try:
                                gid_num3 = _assign_group_numeric_id(ctx, layer, raw_mkey)
                                rv[7] = str(gid_num3)
                            except Exception:
                                rv[7] = raw_mkey
                            rows.append(rv)
            return
        except Exception:
            # Fallback: at least output one row with pattern fields
            r = make_row()
            try:
                pname = safe_get(e.dxf, "pattern_name", "")
                pscale = safe_get(e.dxf, "pattern_scale", "")
                pangle = safe_get(e.dxf, "pattern_angle", "")
                solid = safe_get(e.dxf, "solid_fill", "")
                r[28] = pname
                r[29] = pscale
                r[30] = pangle
                r[31] = solid
            except Exception:
                pass
            r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
            rows.append(r)
            return

    # Other entities: try generic location fields
    loc_candidates = ("insert", "location", "center", "start")
    for name in loc_candidates:
        if hasattr(e.dxf, name):
            x, y, z = point_to_xyz(getattr(e.dxf, name))
            r = make_row()
            if x is not None and y is not None:
                set_xyz(r, x, y, z or 0.0)
            r[6] = text_content(e)
            r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
            rows.append(r)
            return
    # If nothing, output empty placeholder row
    r = make_row()
    r[6] = text_content(e)
    r[8] = str(_assign_module_numeric_id(ctx, layer, module_key)) if (ctx and module_key) else ""
    rows.append(r)


def export_dxf_to_csv(dxf_path: Path, csv_path: Path):
    doc = ezdxf.readfile(str(dxf_path))
    rows = []

    # 编号上下文
    ctx = {
        "group_id_map": {},
        "next_group_id": 1,
        "module_id_map": {},
        "next_module_id": 1,
        "module_id_to_block_name": {},
    }

    # 遍历模型空间与图纸空间
    spaces = [(doc.modelspace(), "Model")]
    try:
        for psp in doc.paperspace_layouts():
            try:
                nm = getattr(psp, "name", None) or getattr(psp.layout, "name", None) or "Paper"
            except Exception:
                nm = "Paper"
            spaces.append((psp, nm))
    except Exception:
        pass

    for sp, sp_name in spaces:
        for e in sp:
            try:
                handle_entity(e, rows, sp_name, ctx=ctx)
            except Exception:
                # 单个实体失败不影响整体导出
                continue

    # 写 CSV（扩展字段）
    headers = [
        "X", "Y", "Z", "Layer", "SubClass", "Linetype", "Text", "GroupId", "ModuleId",
        "EntityHandle", "OwnerHandle", "VertexIndex", "Bulge", "StartWidth", "EndWidth",
        "StartX", "StartY", "StartZ", "EndX", "EndY", "EndZ",
        "CenterX", "CenterY", "CenterZ", "Radius", "StartAngle", "EndAngle", "IsClosed",
        "HatchPatternName", "HatchPatternScale", "HatchPatternAngle", "HatchSolid",
        "HatchPathIndex", "HatchPathIsHole", "HatchPathType",
        "Space", "ModuleFather", "ModuleNowID", "ModuleRoot", "ModuleName",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        mapping = ctx.get("module_nowid_to_block_name", {})
        for r in rows:
            try:
                mod_now_id = str(r[37])
            except Exception:
                mod_now_id = ""
            mod_name = mapping.get(mod_now_id, "")
            writer.writerow(r + [mod_name])

    # 不再输出单独的映射文件，映射已写入主表的 ModuleName 列


def main(output_dir: Path = None):
    global OUTPUT_DIR
    if output_dir is None:
        output_dir = OUTPUT_DIR
    else:
        OUTPUT_DIR = output_dir

    in_path = "桩位图_抗拔反力_标注.dxf"
    out_path = output_dir / "桩位图_抗拔反力_标注.csv"

    try:
        export_dxf_to_csv(in_path, out_path)
    except ezdxf.DXFStructureError as e:
        print(f"DXF 结构错误：{e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"导出失败：{e}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=str(OUTPUT_DIR), help="输出目录")
    args = parser.parse_args()
    main(Path(args.out))