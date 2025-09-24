#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Annotate DXF with pile type notes based on CSV mappings.

- Reads 桩位图.csv and table_Model_grid_1.csv
- Matches 桩位图.csv's ModuleName to table_Model_grid_1.csv's "桩图例"
- For each unique ModuleNowID, writes table's "未注明的选用桩型" near the element's coordinates
- Saves to a new DXF file

Notes:
- Coordinates are auto-detected from common column names (InsertX/InsertY, X/Y, etc.)
- Configurable parameters are set via direct assignment below per user's preference
- Requires: ezdxf (pip install ezdxf)
"""

from __future__ import annotations

import csv
import os
import sys
from typing import Dict, List, Optional, Tuple

# =====================
# Configuration (edit these as needed)
# =====================
# Absolute paths as provided
PATH_PILE_CSV = r".\outputs\桩位图_抗拔反力_标注_文字.csv"
PATH_INPUT_DXF = r".\桩位图_抗拔反力_标注_文字.dxf"

PATH_TABLE_CSV = r".\dxf_tables\table_Model_grid_1.csv"

PATH_OUTPUT_DXF = r".\桩位图_图标标注.dxf"
# 新增：输出带“选用桩型”列的CSV
PATH_OUTPUT_CSV = r".\outputs\桩位图_图标标注.csv"

# Text placement & style
TEXT_PREFIX = "未注明的选用桩型："  # Set to "" if you prefer only the value
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
FIELD_TABLE_VALUE_CANDIDATES = [
	"未注明的选用桩型",
	"桩型备注",
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
	mapping: Dict[str, str] = {}
	if not table_rows:
		return mapping
	# Resolve table fields
	proto_row = table_rows[0]
	key_field = _get_first_field(proto_row, FIELD_TABLE_KEY_CANDIDATES)
	val_field = _get_first_field(proto_row, FIELD_TABLE_VALUE_CANDIDATES)
	if not key_field or not val_field:
		raise KeyError("无法在表CSV中找到 '桩图例' 或 '未注明的选用桩型' 字段")
	for row in table_rows:
		key = row.get(key_field)
		val = row.get(val_field)
		if key is None or val is None:
			continue
		k = _casefold(key)
		if k and k not in mapping:
			mapping[k] = val.strip()
	return mapping


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
		pile_type = table_map.get(lookup_key)
		if pile_type is None or pile_type.strip() == "":
			# No mapping found; skip
			continue
		label_text = f"{TEXT_PREFIX}{pile_type}" if TEXT_PREFIX else pile_type
		annotations.append((module_id, x_val, y_val, label_text))
		seen_ids.add(module_id)

	return annotations


# 新增：构建 模块ID->选用桩型 的映射（基于表映射与桩位图）

def build_module_id_to_type_map(
	pile_rows: List[Dict[str, str]],
	table_map: Dict[str, str],
) -> Dict[str, str]:
	id_to_type: Dict[str, str] = {}
	if not pile_rows:
		return id_to_type
	proto = pile_rows[0]
	module_field = _get_first_field(proto, FIELD_MODULE_NAME_CANDIDATES)
	id_field = _get_first_field(proto, FIELD_MODULE_NOW_ID_CANDIDATES)
	if not module_field or not id_field:
		raise KeyError("无法在桩位图CSV中找到 'ModuleName' 或 'ModuleNowID' 字段以写入选用桩型列")
	for row in pile_rows:
		module_name = row.get(module_field)
		module_id = row.get(id_field)
		if module_id is None or module_name is None:
			continue
		module_id = module_id.strip()
		if module_id == "":
			continue
		lookup_key = _casefold(module_name)
		pile_type = table_map.get(lookup_key)
		if pile_type is not None and pile_type.strip() != "":
			id_to_type[module_id] = pile_type.strip()
	return id_to_type


# 新增：写出在原CSV基础上追加“选用桩型”列的新CSV

def write_augmented_csv_with_type(
	pile_rows: List[Dict[str, str]],
	output_csv_path: str,
	id_to_type: Dict[str, str],
) -> None:
	if not pile_rows:
		# 仍写出仅包含表头（如果无法检测则写出只有“选用桩型”一列）
		os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)
		try:
			with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
				writer = csv.DictWriter(f, fieldnames=["选用桩型"])
				writer.writeheader()
		except Exception:
			pass
		return

	# 保留原有列顺序，并在末尾追加“选用桩型”列（如已存在则沿用位置）
	original_fieldnames = list(pile_rows[0].keys())
	if "选用桩型" in original_fieldnames:
		fieldnames = original_fieldnames
	else:
		fieldnames = original_fieldnames + ["选用桩型"]

	# 检测模块ID字段名
	id_field = _get_first_field(pile_rows[0], FIELD_MODULE_NOW_ID_CANDIDATES)
	if not id_field:
		raise KeyError("无法在桩位图CSV中找到 'ModuleNowID' 字段以写入选用桩型列")

	# 确保目录存在
	os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)

	with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		for row in pile_rows:
			module_id = (row.get(id_field) or "").strip()
			type_val = id_to_type.get(module_id, "")
			out_row = dict(row)
			out_row["选用桩型"] = type_val
			writer.writerow(out_row)


# 新增：写出在原CSV基础上合并表中除“桩图例”外的所有字段，并确保“选用桩型”列存在

def write_augmented_csv_with_table_fields(
	pile_rows: List[Dict[str, str]],
	table_rows: List[Dict[str, str]],
	output_csv_path: str,
) -> None:
	if not pile_rows:
		# 若无桩位行，仍写出表头：保持至少“选用桩型”
		os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)
		table_headers = list(table_rows[0].keys()) if table_rows else []
		# 去除“桩图例”
		table_headers = [h for h in table_headers if h.strip() != "桩图例"]
		fieldnames = ["选用桩型"] + table_headers
		with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
			writer = csv.DictWriter(f, fieldnames=fieldnames)
			writer.writeheader()
		return

	# 检测桩位图的关键列
	proto = pile_rows[0]
	module_field = _get_first_field(proto, FIELD_MODULE_NAME_CANDIDATES)
	id_field = _get_first_field(proto, FIELD_MODULE_NOW_ID_CANDIDATES)
	if not module_field or not id_field:
		raise KeyError("无法在桩位图CSV中找到 'ModuleName' 或 'ModuleNowID' 字段以增广表字段")

	# 检测表的关键列
	if not table_rows:
		raise KeyError("表CSV为空，无法增广表字段")
	proto_table = table_rows[0]
	table_key_field = _get_first_field(proto_table, FIELD_TABLE_KEY_CANDIDATES)
	if not table_key_field:
		raise KeyError("无法在表CSV中找到 '桩图例' 字段")

	# 构建表映射：key(casefold) -> 行(dict)
	row_map: Dict[str, Dict[str, str]] = {}
	for trow in table_rows:
		k_raw = trow.get(table_key_field)
		if k_raw is None:
			continue
		k = _casefold(k_raw)
		if k and k not in row_map:
			row_map[k] = trow

	# 计算输出表头
	original_fieldnames = list(pile_rows[0].keys())
	# 表字段（去除 key）
	table_fieldnames = [h for h in proto_table.keys() if h.strip() != "桩图例"]
	# 追加所有表字段中不存在于桩位图的列
	aug_fields = [h for h in table_fieldnames if h not in original_fieldnames]
	# 确保“选用桩型”存在
	fieldnames = list(original_fieldnames)
	if "选用桩型" not in fieldnames:
		fieldnames.append("选用桩型")
	# 再追加表其他字段
	for h in aug_fields:
		if h not in fieldnames:
			fieldnames.append(h)

	# 写文件
	os.makedirs(os.path.dirname(output_csv_path) or ".", exist_ok=True)
	with open(output_csv_path, "w", encoding="utf-8-sig", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		for row in pile_rows:
			out_row = dict(row)
			module_name = row.get(module_field)
			k = _casefold(module_name) if module_name is not None else ""
			trow = row_map.get(k)
			# 填充“选用桩型”列（来源优先级：未注明的选用桩型、桩型备注）
			type_val = ""
			if trow is not None:
				for cand in FIELD_TABLE_VALUE_CANDIDATES:
					v = trow.get(cand)
					if v is not None and v.strip() != "":
						type_val = v.strip()
						break
			out_row["选用桩型"] = type_val
			# 合并表的其他字段（不覆盖桩位图已有同名列）
			if trow is not None:
				for h in table_fieldnames:
					if h not in out_row:
						out_row[h] = trow.get(h, "") or ""
			writer.writerow(out_row)


# 新增：从增广CSV（包含“选用桩型”列）构建用于DXF的标注

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

	# 定位“选用桩型”字段名（保持原表头名）
	type_field: Optional[str] = None
	for k in proto.keys():
		if k.strip() == "选用桩型":
			type_field = k
			break
	if not type_field:
		raise KeyError("增广CSV中未找到‘选用桩型’列")

	seen_ids: set[str] = set()
	for row in augmented_rows:
		module_id = (row.get(id_field) or "").strip()
		if not module_id or module_id in seen_ids:
			continue
		x_val = _parse_float_safe(row.get(x_field))
		y_val = _parse_float_safe(row.get(y_field))
		type_val = (row.get(type_field) or "").strip()
		if x_val is None or y_val is None:
			continue
		if type_val == "":
			# 无选用桩型则不标注
			continue
		label_text = f"{TEXT_PREFIX}{type_val}" if TEXT_PREFIX else type_val
		annotations.append((module_id, x_val, y_val, label_text))
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
		doc.appids.new("ANNOTATION")
	except Exception:
		pass

	# Ensure layer exists for annotation texts
	try:
		if "DEBUG表格" not in doc.layers:
			doc.layers.add("DEBUG表格")
	except Exception:
		pass

	for module_id, x, y, text in annotations:
		px = x + offset_x
		py = y + offset_y
		if use_mtext:
			mtext = msp.add_mtext(text, dxfattribs={"height": text_height, "width": mtext_width, "layer": "DEBUG表格"})
			# Place by insert point for wider compatibility
			mtext.dxf.insert = (px, py)
			try:
				mtext.set_xdata("ANNOTATION", [(1000, f"ModuleNowID={module_id}")])
			except Exception:
				pass
		else:
			ent = msp.add_text(text, dxfattribs={"height": text_height, "layer": "DEBUG表格"})
			# Place by insert point for compatibility with ezdxf versions lacking set_pos
			ent.dxf.insert = (px, py)
			try:
				ent.set_xdata("ANNOTATION", [(1000, f"ModuleNowID={module_id}")])
			except Exception:
				pass

	doc.saveas(output_dxf_path)


def main() -> None:
	print("[INFO] 开始处理……")
	print(f"[INFO] 读取桩位图: {PATH_PILE_CSV}")
	pile_rows = _read_csv_dicts(PATH_PILE_CSV)
	print(f"[INFO] 读取表数据: {PATH_TABLE_CSV}")
	table_rows = _read_csv_dicts(PATH_TABLE_CSV)

	table_map = build_table_mapping(table_rows)
	print(f"[INFO] 表映射数量: {len(table_map)}")

	# 先输出增广CSV：表中除“桩图例”外的所有字段全部追加，并确保“选用桩型”列
	try:
		print(f"[INFO] 写出增广CSV(含表字段): {PATH_OUTPUT_CSV}")
		write_augmented_csv_with_table_fields(pile_rows, table_rows, PATH_OUTPUT_CSV)
		print(f"[OK] 已保存CSV: {PATH_OUTPUT_CSV}")
	except Exception as exc:
		print(f"[WARN] 写出CSV失败: {exc}")

	# 基于增广CSV生成DXF标注
	print(f"[INFO] 读取用于标注的增广CSV: {PATH_OUTPUT_CSV}")
	augmented_rows = _read_csv_dicts(PATH_OUTPUT_CSV)
	csv_annotations = collect_annotations_from_augmented_csv(augmented_rows)
	print(f"[INFO] 基于CSV的计划标注数量: {len(csv_annotations)}")

	if not csv_annotations:
		print("[WARN] 没有可标注的数据（CSV中‘选用桩型’为空），退出。")
		return

	# Ensure output directory exists
	os.makedirs(os.path.dirname(PATH_OUTPUT_DXF), exist_ok=True)
	print(f"[INFO] 读取并标注 DXF: {PATH_INPUT_DXF}")
	try:
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
	except FileNotFoundError as e:
		print(f"[ERROR] 文件不存在: {e}", file=sys.stderr)
		sys.exit(1)
	except KeyError as e:
		print(f"[ERROR] 字段缺失: {e}", file=sys.stderr)
		sys.exit(2)
	except Exception as e:
		print(f"[ERROR] 处理失败: {e}", file=sys.stderr)
		sys.exit(3)


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