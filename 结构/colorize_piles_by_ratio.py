#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ---------- Configuration ----------
INPUT_CSV = Path("outputs") / "桩位图_final.csv"
INPUT_DXF = Path("桩位图.dxf")
OUTPUT_DXF =  Path("outputs") / "桩位图_final.dxf"

# New layers and colors (AutoCAD color index)
LAYER_LOW = "R_RATIO_LT_0_3"   # ratio < 0.3 (green)
LAYER_MID = "R_RATIO_0_3_0_6"  # 0.3 <= ratio <= 0.6 (yellow)
LAYER_HIGH = "R_RATIO_GT_0_6"  # ratio > 0.6 (red)

LAYER_COLORS = {
	LAYER_LOW: 3,   # Green
	LAYER_MID: 2,   # Yellow
	LAYER_HIGH: 1,  # Red
}

# Position matching tolerance (drawing units)
POSITION_TOLERANCE = 1e-3

# ---------- Helpers ----------

def _try_import_ezdxf():
	try:
		import ezdxf  # type: ignore
		return ezdxf
	except Exception as exc:
		print("[ERROR] 需要安装 ezdxf 库：pip install ezdxf", file=sys.stderr)
		print(f"[DETAIL] {exc}", file=sys.stderr)
		raise


def _read_csv_dicts(path: Path) -> List[Dict[str, str]]:
	# Try UTF-8 with BOM first, then GBK as fallback
	encodings = ["utf-8-sig", "utf-8", "gbk"]
	for enc in encodings:
		try:
			with path.open("r", encoding=enc, newline="") as f:
				reader = csv.DictReader(f)
				return [
					{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
					for row in reader
				]
		except UnicodeDecodeError:
			continue
		except FileNotFoundError:
			raise
	raise UnicodeDecodeError("", b"", 0, 1, "Unable to decode CSV with common encodings")


def _to_float(text: Optional[str]) -> Optional[float]:
	if text is None:
		return None
	try:
		s = text.replace(",", "").replace("kN", "").strip()
		if s == "":
			return None
		return float(s)
	except Exception:
		return None


def _classify_ratio(ratio: float) -> Optional[str]:
	if ratio < 0.3:
		return LAYER_LOW
	if 0.3 <= ratio <= 0.6:
		return LAYER_MID
	if ratio > 0.6:
		return LAYER_HIGH
	return None


def _almost_equal(a: float, b: float, tol: float = POSITION_TOLERANCE) -> bool:
	return abs(a - b) <= tol


def _ensure_layers(doc, layer_to_color: Dict[str, int]) -> None:
	for lname, color in layer_to_color.items():
		try:
			if lname not in doc.layers:
				doc.layers.add(lname, dxfattribs={"color": int(color)})
			layer = doc.layers.get(lname)
			layer.dxf.color = int(color)
			# Ensure layer is visible/unlocked
			try:
				layer.dxf.off = 0
				layer.dxf.freeze = 0
				layer.dxf.lock = 0
			except Exception:
				pass
		except Exception:
			# Be tolerant – layer creation should not abort the entire process
			pass


def _group_rows_by_module(rows: Iterable[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
	by_id: Dict[str, List[Dict[str, str]]] = {}
	for r in rows:
		mid = (r.get("ModuleNowID") or "").strip()
		if not mid:
			continue
		by_id.setdefault(mid, []).append(r)
	return by_id


def _select_insert_row(rows: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
	# Prefer the row whose SubClass (entity type) looks like INSERT; fallback to the first row
	for r in rows:
		if (r.get("SubClass") or "").strip().upper() == "INSERT":
			return r
	return rows[0] if rows else None


def _find_matching_insert(spaces: List, name: str, x: float, y: float):
	# Look for an INSERT with same name & near the given coordinates
	for space in spaces:
		try:
			for e in space.query("INSERT"):
				if (e.dxf.name or "") != name:
					continue
				e_x = float(getattr(e.dxf.insert, "x", getattr(e.dxf.insert, 0.0)))
				e_y = float(getattr(e.dxf.insert, "y", getattr(e.dxf.insert, 0.0)))
				if _almost_equal(e_x, x) and _almost_equal(e_y, y):
					return e
		except Exception:
			continue
	return None


def _duplicate_insert_to_layer(space, src_insert, target_layer: str):
	# Duplicate the INSERT with same parameters onto a target layer, then explode so geometry is on that layer
	try:
		name = src_insert.dxf.name
		insert = src_insert.dxf.insert
		rotation = float(getattr(src_insert.dxf, "rotation", 0.0) or 0.0)
		xscale = float(getattr(src_insert.dxf, "xscale", 1.0) or 1.0)
		yscale = float(getattr(src_insert.dxf, "yscale", 1.0) or 1.0)
		scale = float(getattr(src_insert.dxf, "scale", 1.0) or 1.0)

		new_ref = space.add_blockref(name, insert, dxfattribs={
			"layer": target_layer,
			"rotation": rotation,
			"xscale": xscale if xscale else scale,
			"yscale": yscale if yscale else scale,
			"color": 256,  # BYLAYER
		})
		# Attempt to copy visible attributes if present (best-effort)
		try:
			values = {}
			for a in src_insert.attribs():
				tag = getattr(a.dxf, "tag", None)
				text = getattr(a, "text", getattr(a.dxf, "text", None))
				if tag and text is not None:
					values[tag] = text
			if values:
				new_ref.add_auto_attribs(values)
		except Exception:
			pass

		# Explode to ensure geometry resides on the target layer
		try:
			created = new_ref.explode()
			for ent in created:
				try:
					ent.dxf.layer = target_layer
					# Set ByLayer color where supported
					if hasattr(ent.dxf, "color"):
						ent.dxf.color = 256
				except Exception:
					continue
			# Remove the temporary block reference
			try:
				space.delete_entity(new_ref)
			except Exception:
				try:
					new_ref.destroy()
				except Exception:
					pass
		except Exception:
			# If explode fails, keep the blockref as-is
			pass

		return new_ref
	except Exception:
		return None


# ---------- Main Logic ----------

def process() -> int:
	ezdxf = _try_import_ezdxf()

	if not INPUT_CSV.exists():
		print(f"[ERROR] 输入CSV不存在: {INPUT_CSV}", file=sys.stderr)
		return 2
	if not INPUT_DXF.exists():
		print(f"[ERROR] 输入DXF不存在: {INPUT_DXF}", file=sys.stderr)
		return 2

	rows = _read_csv_dicts(INPUT_CSV)
	if not rows:
		print("[WARN] 输入CSV为空，无需处理")
		return 0

	# Build module groups and pick the INSERT row per module
	by_mid = _group_rows_by_module(rows)
	selected_rows: List[Dict[str, str]] = []
	for mid, group in by_mid.items():
		ins = _select_insert_row(group)
		if not ins:
			continue
		# Need required fields
		name = (ins.get("ModuleName") or "").strip()
		x = _to_float(ins.get("X"))
		y = _to_float(ins.get("Y"))
		ratio = _to_float(ins.get("承载力比值"))
		if (not name) or (x is None) or (y is None) or (ratio is None):
			continue
		layer = _classify_ratio(ratio)
		if not layer:
			continue
		ins["__target_layer__"] = layer
		selected_rows.append(ins)

	if not selected_rows:
		print("[WARN] 没有需要复制的桩（可能缺少有效的承载力比值）")
		return 0

	doc = ezdxf.readfile(str(INPUT_DXF))
	# Collect spaces: model + all paper layouts
	spaces = [doc.modelspace()]
	try:
		for psp in doc.paperspace_layouts():
			spaces.append(psp)
	except Exception:
		pass

	_ensure_layers(doc, LAYER_COLORS)

	# Index spaces by name for reporting (optional)
	copied = 0
	for r in selected_rows:
		name = r["ModuleName"].strip()
		x = float(_to_float(r.get("X")) or 0.0)
		y = float(_to_float(r.get("Y")) or 0.0)
		target_layer = r["__target_layer__"]

		src = _find_matching_insert(spaces, name, x, y)
		if src is None:
			# Fallback: insert a new ref at CSV coordinates with default scale/rotation, then explode
			try:
				msp = doc.modelspace()
				ref = msp.add_blockref(name, (x, y, 0.0), dxfattribs={"layer": target_layer, "color": 256})
				try:
					created = ref.explode()
					for ent in created:
						try:
							ent.dxf.layer = target_layer
							if hasattr(ent.dxf, "color"):
								ent.dxf.color = 256
						except Exception:
							continue
					try:
						msp.delete_entity(ref)
					except Exception:
						try:
							ref.destroy()
						except Exception:
							pass
				except Exception:
					pass
				copied += 1
			except Exception:
				continue
			continue

		# Duplicate with same parameters
		space = src.dxf.owner_handle
		# The owner handle approach is not needed; simply add to the same space as src
		try:
			new_ent = _duplicate_insert_to_layer(src.drawing_entity_space, src, target_layer)
		except Exception:
			# Some ezdxf versions may not expose drawing_entity_space; use doc.entitydb instead
			new_ent = _duplicate_insert_to_layer(doc.modelspace(), src, target_layer)
		if new_ent is not None:
			copied += 1

	doc.saveas(str(OUTPUT_DXF))
	print(f"[OK] 已复制 {copied} 个桩到分层，并保存为: {OUTPUT_DXF}")
	return 0


def main(argv: Optional[List[str]] = None) -> int:
	try:
		return process()
	except Exception as exc:
		print(f"[FATAL] 处理失败: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main()) 