#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import math
from pathlib import Path
import re
import sys
from typing import Dict, Iterable, List, Optional, Tuple
from compute_num_from_N import compute_x1_for_row


TABLE_DEFAULT = Path("dxf_tables") / "table_Model_grid_1.csv"
ICON_CSV_DEFAULT = Path("outputs") / "桩位图_图标标注.csv"
OUTPUT_DEFAULT = Path("outputs") / "抗拔桩_参数计算.csv"


def detect_encoding(path: Path) -> str:
	return "utf-8-sig"


def find_field(fieldnames: List[str], keyword: str) -> Optional[str]:
	if not fieldnames:
		return None
	for name in fieldnames:
		if keyword in (name or ""):
			return name
	return None


def parse_float(value: str) -> Optional[float]:
	value = (value or "").strip()
	if value == "":
		return None
	try:
		return float(value)
	except Exception:
		# Try to normalize commas or stray characters
		try:
			return float(value.replace(",", "").replace("kN", "").strip())
		except Exception:
			return None


def build_module_force_map(icon_csv: Path) -> Dict[str, float]:
	"""Return mapping: ModuleName -> max absolute 反力.

	If multiple rows share same ModuleName, take max(abs(反力)).
	"""
	module_to_max_abs: Dict[str, float] = {}
	with icon_csv.open("r", encoding=detect_encoding(icon_csv), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			raise ValueError("图标标注CSV缺少表头")
		name_col = find_field(reader.fieldnames, "ModuleName")
		force_col = find_field(reader.fieldnames, "反力")
		if name_col is None or force_col is None:
			raise KeyError("在图标标注CSV中未找到所需列：ModuleName 或 反力")
		for row in reader:
			name = (row.get(name_col) or "").strip()
			if name == "":
				continue
			force_val = parse_float(row.get(force_col) or "")
			if force_val is None:
				continue
			abs_val = abs(force_val)
			prev = module_to_max_abs.get(name)
			if prev is None or abs_val > prev:
				module_to_max_abs[name] = abs_val
	return module_to_max_abs


def load_concrete_tensile(json_path: Path) -> Dict[str, float]:
	"""Map concrete grade (e.g., C30) -> 混凝土轴心抗拉强度标准值 (ftk)."""
	with json_path.open("r", encoding="utf-8") as f:
		data = json.load(f)
		items = data.get("data", [])
		result: Dict[str, float] = {}
		for it in items:
			grade = str(it.get("concrete_strength", "")).strip()
			ftk = it.get("tensile_strength_standard_value")
			if grade and ftk is not None:
				result[grade] = float(ftk)
		return result


def load_steel_emod(json_path: Path) -> Dict[str, float]:
	"""Map steel 符号 (e.g., %%132) -> 弹性模量Es (N/mm²)."""
	with json_path.open("r", encoding="utf-8") as f:
		data = json.load(f)
		items = data.get("数据", [])
		result: Dict[str, float] = {}
		for it in items:
			symbol = str(it.get("符号", "")).strip()
			E_txt = it.get("弹性模量E_s（×10⁵N/mm²）")
			if symbol and E_txt is not None:
				try:
					result[symbol] = float(E_txt) * 1e5
				except Exception:
					continue
		return result


def parse_diameter_mm_from_type(pile_type: str) -> Optional[int]:
	"""Parse pile diameter d (mm) from second '-' segment of 未注明的选用桩型.

	Recognizes:
	- D600
	- %%131600 or %%132600 (%%131/%%132 denote a symbol)
	- Fallback: last integer group in the second segment
	"""
	if not pile_type:
		return None
	tokens = pile_type.split("-")
	if len(tokens) < 2:
		return None
	segment2 = tokens[1]
	m = re.search(r"D\s*(\d{2,4})", segment2, flags=re.IGNORECASE)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			pass
	m = re.search(r"%%13[12]\s*(\d{2,4})", segment2)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			pass
	matches = re.findall(r"(\d{2,4})", segment2)
	if matches:
		try:
			return int(matches[-1])
		except Exception:
			return None
	return None


def extract_grade(pile_type: str) -> Optional[str]:
	if not pile_type:
		return None
	tokens = pile_type.split("-")
	if not tokens:
		return None
	return tokens[-1].strip()


def parse_rebar_from_type(pile_type: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
	"""Parse rebar info at 1/3L(...) from 未注明的选用桩型.

	Returns (count, diameter_mm, symbol) or (None, None, None) if unavailable.
	Ignores 2/3L section.
	"""
	if not pile_type:
		return None, None, None
	tokens = pile_type.split("-")
	# Find token that starts with '1/3L'
	rebar_token = None
	for tok in tokens:
		if tok.strip().startswith("1/3L"):
			rebar_token = tok
			break
	if rebar_token is None:
		return None, None, None
	# Extract inside parentheses
	m = re.search(r"1/3L\s*\(([^)]*)\)", rebar_token)
	if not m:
		return None, None, None
	inner = m.group(1)
	# Try to capture like '10%%13222' => count=10, symbol=%%132, dia=22
	m2 = re.search(r"(\d+)\s*(%%13[12])\s*(\d{2,3})", inner)
	if m2:
		try:
			return int(m2.group(1)), int(m2.group(3)), m2.group(2)
		except Exception:
			return None, None, None
	# Fallback: capture without symbol
	m3 = re.search(r"(\d+)\s*(\d{2,3})", inner)
	if m3:
		try:
			return int(m3.group(1)), int(m3.group(2)), None
		except Exception:
			return None, None, None
	return None, None, None


def extract_uplift_with_aug(table_csv: Path, icon_csv: Path, output_csv: Path, concrete_json: Path, steel_json: Path) -> int:
	"""Compute required parameters for uplift piles and write CSV.

	Outputs columns: w, Aps, As, deq, acr, ftk, Nk, Es, c, rou, sig, psi
	"""
	module_force = build_module_force_map(icon_csv)
	ftk_map = load_concrete_tensile(concrete_json)
	Es_map = load_steel_emod(steel_json)
	count = 0
	with table_csv.open("r", encoding=detect_encoding(table_csv), newline="") as f:
		reader = csv.DictReader(f)
		uplift_col = find_field(reader.fieldnames or [], "单桩竖向抗拔承载力特征值")
		type_col = find_field(reader.fieldnames or [], "未注明的选用桩型")
		legend_col = find_field(reader.fieldnames or [], "桩图例")
		missing: List[str] = []
		if uplift_col is None:
			missing.append("单桩竖向抗拔承载力特征值")
		if type_col is None:
			missing.append("未注明的选用桩型")
		if legend_col is None:
			missing.append("桩图例")
		if missing:
			raise KeyError("表格缺少列: " + ", ".join(missing))

		rows_out: List[Dict[str, str]] = []
		for row in reader:
			uplift_val_raw = (row.get(uplift_col) or "").strip()
			if uplift_val_raw == "":
				continue  # not uplift pile
			pile_type = (row.get(type_col) or "").strip()
			legend = (row.get(legend_col) or "").strip()
			Nk = module_force.get(legend) * 1000 # KN -> N
			# Aps from pile diameter
			d_mm = parse_diameter_mm_from_type(pile_type)
			Aps = None if d_mm is None else (math.pi / 4.0) * (d_mm ** 2)
			# Rebar at 1/3L
			n_bars, dia_bar, sym_bar = parse_rebar_from_type(pile_type)
			As = None
			deq = None
			if n_bars is not None and dia_bar is not None:
				As = n_bars * (math.pi / 4.0) * (dia_bar ** 2)
				deq = dia_bar
			# ftk by grade
			grade = extract_grade(pile_type) or ""
			ftk = ftk_map.get(grade)
			# Es by symbol
			Es = Es_map.get(sym_bar or "", None)
			# Constants
			w = 0.3
			acr = 2.7
			c_val = 50
			# Derived ratios
			rou = None
			if As and Aps and Aps != 0:
				rou = As / Aps
			sig = None
			if Nk is not None and As and As != 0:
				sig = Nk / As
			psi = None
			if ftk is not None and rou and rou != 0 and sig and sig != 0:
				psi_val = 1.1 - 0.65 * ftk / (rou * sig)
				if psi_val < 0.2:
					print(f"警告: 桩图例 {legend} 的psi计算值 {psi_val:.4f} < 0.2，已按0.2计", file=sys.stderr)
					psi_val = 0.2
				psi = psi_val

			# 右式 = acr * psi * sig / Es * (1.9 * c + 0.08 * deq / rou)
			right_value = ""
			try:
				if (acr is not None) and (psi is not None) and (sig is not None) and (Es is not None) and (c_val is not None) and (deq is not None) and (rou is not None) and (rou != 0) and (Es != 0):
					term = 1.9 * float(c_val) + 0.08 * float(deq) / float(rou)
					val = float(acr) * float(psi) * float(sig) / float(Es) * term
					right_value = f"{val}"
			except Exception:
				right_value = ""

			rows_out.append({
				"w": ("" if w is None else f"{w}"),
				"Aps(mm^2)": ("" if Aps is None else f"{Aps}"),
				"As(mm^2)": ("" if As is None else f"{As}"),
				"deq(mm)": ("" if deq is None else f"{deq}"),
				"目前钢筋数量": ("" if n_bars is None else f"{n_bars}"),
				"acr": ("" if acr is None else f"{acr}"),
				"ftk(N/mm^2)": ("" if ftk is None else f"{ftk}"),
				"Nk(N)": ("" if Nk is None else f"{Nk}"),
				"Es(N/mm^2)": ("" if Es is None else f"{Es}"),
				"c(mm)": ("" if c_val is None else f"{c_val}"),
				"rou": ("" if rou is None else f"{rou}"),
				"sig(N/mm^2)": ("" if sig is None else f"{sig}"),
				"psi": ("" if psi is None else f"{psi}"),
				"右式": right_value,
				"未注明的选用桩型": pile_type,
				"桩图例": legend,
			})
			count += 1

	output_csv.parent.mkdir(parents=True, exist_ok=True)
	# First write (without 优化后钢筋数量)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out:
		fieldnames = [
			"w",
			"Aps(mm^2)",
			"As(mm^2)",
			"deq(mm)",
			"目前钢筋数量",
			"acr",
			"ftk(N/mm^2)",
			"Nk(N)",
			"Es(N/mm^2)",
			"c(mm)",
			"rou",
			"sig(N/mm^2)",
			"psi",
			"右式",
			"未注明的选用桩型",
			"桩图例",
		]
		writer = csv.DictWriter(f_out, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows_out)

	# Compute and append 优化后钢筋数量 using compute_x1_for_row per row
	augmented_rows: List[Dict[str, str]] = []
	for idx, r in enumerate(rows_out, start=1):
		y_txt = r.get("Nk(N)") or ""
		try:
			y_val = float(y_txt) if y_txt != "" else None
		except Exception:
			y_val = None
		x1_val = ""
		if y_val is not None:
			try:
				x1 = compute_x1_for_row(y=y_val, row_index=idx, input_csv=output_csv)
				# 上取整；若为 NaN 则写入 6
				if isinstance(x1, float) and (math.isnan(x1)):
					min_opt = 6
				else:
					min_opt = int(math.ceil(float(x1)))
				x1_val = f"{min_opt}"
			except Exception:
				x1_val = ""
		row2 = dict(r)
		row2["优化后钢筋数量"] = x1_val
		augmented_rows.append(row2)

	# Second write (with 优化后钢筋数量)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out2:
		fieldnames2 = [
			"w",
			"Aps(mm^2)",
			"As(mm^2)",
			"deq(mm)",
			"目前钢筋数量",
			"acr",
			"ftk(N/mm^2)",
			"Nk(N)",
			"Es(N/mm^2)",
			"c(mm)",
			"rou",
			"sig(N/mm^2)",
			"psi",
			"右式",
			"未注明的选用桩型",
			"桩图例",
			"优化后钢筋数量",
			"描述",
		]
		writer2 = csv.DictWriter(f_out2, fieldnames=fieldnames2)
		writer2.writeheader()
		for r in augmented_rows:
			# Build 描述
			try:
				Nk_txt = (r.get("Nk(N)") or "").strip()
				Nk_val = float(Nk_txt) if Nk_txt != "" else None
				right_txt = (r.get("右式") or "").strip()
				right_val = float(right_txt) if right_txt != "" else None
				w_txt = (r.get("w") or "").strip()
				w_val = float(w_txt) if w_txt != "" else None
				before_cnt_txt = (r.get("目前钢筋数量") or "").strip()
				before_cnt = int(float(before_cnt_txt)) if before_cnt_txt != "" else None
				deq_txt = (r.get("deq(mm)") or "").strip()
				deq_val = float(deq_txt) if deq_txt != "" else None
				after_cnt_txt = (r.get("优化后钢筋数量") or "").strip()
				after_cnt = int(float(after_cnt_txt)) if after_cnt_txt != "" else None
				right_disp = (f"{right_val:.2f}" if right_val is not None else right_txt)
				# compliance text
				comp = ""
				if (right_val is not None) and (w_val is not None):
					if right_val > w_val:
						comp = "超过"
					elif abs(right_val - w_val) <= 0.05:  # 接近阈值：±0.05mm
						comp = "接近"
					else:
						comp = "符合"
				else:
					comp = "符合"
				# upgrade/downgrade text
				chg = "不变"
				if (before_cnt is not None) and (after_cnt is not None):
					if after_cnt < before_cnt:
						chg = "降级"
					elif after_cnt > before_cnt:
						chg = "升级"
				desc = (
					f"该桩型现最大承载的抗拔力为（{Nk_txt} N），桩身最大裂缝宽度为（{right_disp} mm），{comp}规范限值（{w_txt} mm）。"
					f"优化前主筋配置为（{'' if before_cnt is None else before_cnt}）根（{'' if deq_val is None else int(deq_val)}）mm；"
					f"优化后对配置{chg}，优化后钢筋配置为（{'' if after_cnt is None else after_cnt}）根（{'' if deq_val is None else int(deq_val)}）mm。"
				)
			except Exception:
				desc = ""
			r["描述"] = desc
			writer2.writerow(r)

	return count


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description="分析抗拔桩：计算参数并输出CSV")
	parser.add_argument("--table", type=Path, default=TABLE_DEFAULT, help="表格CSV，默认 dxf_tables/table_Model_grid_1.csv")
	parser.add_argument("--icons", type=Path, default=ICON_CSV_DEFAULT, help="图标标注CSV，默认 outputs/桩位图_图标标注.csv")
	parser.add_argument("--concrete", type=Path, default=Path("pre_rule") / "混凝土强度.json", help="混凝土强度JSON，默认 pre_rule/混凝土强度.json")
	parser.add_argument("--steel", type=Path, default=Path("pre_rule") / "钢筋强度.json", help="钢筋强度JSON，默认 pre_rule/钢筋强度.json")
	parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT, help="输出CSV，默认 outputs/抗拔桩_参数计算.csv")
	args = parser.parse_args(argv)

	if not args.table.exists():
		print(f"输入表格不存在: {args.table}", file=sys.stderr)
		return 2
	if not args.icons.exists():
		print(f"图标标注不存在: {args.icons}", file=sys.stderr)
		return 2
	if not args.concrete.exists():
		print(f"混凝土强度不存在: {args.concrete}", file=sys.stderr)
		return 2
	if not args.steel.exists():
		print(f"钢筋强度不存在: {args.steel}", file=sys.stderr)
		return 2

	try:
		count = extract_uplift_with_aug(args.table, args.icons, args.output, args.concrete, args.steel)
		print(f"已输出 {count} 条抗拔桩到: {args.output}")
		return 0
	except Exception as exc:
		print(f"处理失败: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main()) 