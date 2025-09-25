#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import sys
import json
import math
import re
from pathlib import Path
from typing import List, Optional, Dict


def detect_encoding(path: Path) -> str:
	"""Return a likely encoding for the CSV file."""
	# Prefer utf-8-sig to safely strip BOM if present; fall back to gbk if decoding fails elsewhere
	return "utf-8-sig"


def find_field(fieldnames: List[str], keyword: str) -> Optional[str]:
	"""Find the first fieldname containing the keyword substring."""
	if not fieldnames:
		return None
	for name in fieldnames:
		if keyword in (name or ""):
			return name
	return None


def load_concrete_fc(json_path: Path) -> Dict[str, float]:
	"""Load concrete grade -> compressive_strength_design_value mapping."""
	with json_path.open("r", encoding="utf-8") as f:
		data = json.load(f)
		items = data.get("data", [])
		return {str(it.get("concrete_strength", "")).strip(): float(it.get("compressive_strength_design_value")) for it in items if it.get("concrete_strength")}


def parse_diameter_mm(pile_type: str) -> Optional[int]:
	"""Parse diameter in mm from 未注明的选用桩型 second segment.

	Recognizes sequences like:
	- D600
	- %%131600 or %%132600 (where %%131/%%132 denote a symbol)
	- Other digits in the second segment as fallback
	"""
	if not pile_type:
		return None
	tokens = pile_type.split("-")
	if len(tokens) < 2:
		return None
	segment2 = tokens[1]
	# Priority 1: D followed by digits (e.g., D600)
	m = re.search(r"D\s*(\d{2,4})", segment2, flags=re.IGNORECASE)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			pass
	# Priority 2: %%131 or %%132 as a symbol followed by digits (e.g., %%131600 -> 600)
	m = re.search(r"%%13[12]\s*(\d{2,4})", segment2)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			pass
	# Priority 3: take the last integer group in the segment as fallback
	matches = re.findall(r"(\d{2,4})", segment2)
	if matches:
		try:
			return int(matches[-1])
		except Exception:
			return None
	return None


def extract_grade(pile_type: str) -> Optional[str]:
	"""Extract concrete grade from the last '-' segment, e.g., 'C30'."""
	if not pile_type:
		return None
	tokens = pile_type.split("-")
	if not tokens:
		return None
	return tokens[-1].strip()


def _convert_kN_text_to_N(text: str) -> str:
	"""Convert a numeric text possibly with 'kN' to Newtons (×1000).

	Returns empty string if input is empty; returns original text if parsing fails.
	"""
	s = (text or "").strip()
	if s == "":
		return ""
	try:
		clean = s.replace(",", "").replace("kN", "").replace("KN", "").replace("K N", "").replace("k n", "").strip()
		value = float(clean)
		return f"{value * 1000.0}"
	except Exception:
		return s


def extract_compression_piles(input_csv: Path, output_csv: Path, concrete_json: Path) -> int:
	"""Read the table, filter compression piles, and write N, phi, fc, Ap.

	Compression piles: uplift capacity column is empty.
	- N: 单桩竖向抗压承载力特征值（kN）
	- phi: 0.75
	- fc: by grade (last '-' segment in 未注明的选用桩型) from 混凝土强度.json compressive_strength_design_value (N/mm²)
	- Ap: area of bored pile, Ap = π/4 * d^2, where d from the second '-' segment numeric part (mm)
	- 目前混凝土强度: 当前桩型标注的混凝土等级（如 C30）
	- 最低混凝土强度: 满足 N <= phi * Ap * fc 的最低混凝土等级（按 fc 最小且 >= 要求值选取）
	- 目前钢筋数量: 从桩型备注中解析到的钢筋根数（如存在 1/3L(10%%13222) 则为 10），否则留空
	- 最少钢筋数量: 暂置 0（TODO: 之后补充最少钢筋数量计算的逻辑）
	"""

	def _parse_rebar_count_from_type(pile_type_text: str) -> Optional[int]:
		"""Parse current rebar count from the 4th '-' segment like '2/3L(10 %%132 22)'.
		Prefer extracting the integer inside parentheses when a steel symbol '%%13x' is present.
		Returns None if not detectable.
		"""
		if not pile_type_text:
			return None
		parts = pile_type_text.split("-")
		if len(parts) < 4:
			return None
		seg = parts[3]
		# Extract the first parenthesized content
		m = re.search(r"\(([^()]*)\)", seg)
		if not m:
			return None
		inside = m.group(1)
		# Prefer pattern: <count> <symbol> <diameter>, e.g. '10 %%132 22'
		m_sym = re.search(r"^\s*(\d+)\s*(%%13[12])\s*(\d{2,3})\b", inside)
		if m_sym:
			try:
				return int(m_sym.group(1))
			except Exception:
				return None
		# Fallback: take the first integer inside parentheses
		m_int = re.search(r"(\d+)", inside)
		if m_int:
			try:
				return int(m_int.group(1))
			except Exception:
				return None
		return None

	encoding = detect_encoding(input_csv)
	fc_map = load_concrete_fc(concrete_json)
	with input_csv.open("r", encoding=encoding, newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			raise ValueError("CSV appears to have no header row.")

		uplift_col = find_field(reader.fieldnames, "单桩竖向抗拔承载力特征值")
		compress_col = find_field(reader.fieldnames, "单桩竖向抗压承载力特征值")
		type_col = find_field(reader.fieldnames, "未注明的选用桩型")
		legend_col = find_field(reader.fieldnames, "桩图例")
		missing: List[str] = []
		if uplift_col is None:
			missing.append("单桩竖向抗拔承载力特征值（列名包含该关键字）")
		if compress_col is None:
			missing.append("单桩竖向抗压承载力特征值（列名包含该关键字）")
		if type_col is None:
			missing.append("未注明的选用桩型（列名包含该关键字）")
		if missing:
			raise KeyError("无法在表头中找到以下列: " + ", ".join(missing))

		rows_out: List[dict] = []
		for row in reader:
			uplift_val = (row.get(uplift_col) or "").strip()
			if uplift_val != "":
				continue  # 仅保留抗压桩
			pile_type = (row.get(type_col) or "").strip()
			grade = extract_grade(pile_type) or ""
			fc_val = fc_map.get(grade)
			d_mm = parse_diameter_mm(pile_type)
			Ap = None if d_mm is None else (math.pi / 4.0) * (d_mm ** 2)
			n_in_N = _convert_kN_text_to_N((row.get(compress_col) or "").strip())
			# Parse numeric N in Newtons for checks
			try:
				N_val = float(n_in_N) if n_in_N != "" else None
			except Exception:
				N_val = None
			# 右式 = phi * fc * Ap (单位：N)
			right_value = ""
			try:
				if (fc_val is not None) and (Ap is not None):
					phi_val = 0.75
					right_value = f"{phi_val * float(fc_val) * float(Ap)}"
			except Exception:
				right_value = ""

			# 目前混凝土强度（等级名，如 C30）
			current_concrete_grade = grade
			# 计算最低混凝土强度（等级名），使 N <= phi * Ap * fc
			min_concrete_grade = ""
			try:
				phi_val = 0.75
				if (N_val is not None) and (Ap is not None) and (Ap > 0):
					required_fc = float(N_val) / (phi_val * float(Ap))
					# 在 fc_map 中找 fc >= required_fc 的最小 fc 所对应的等级
					candidates = [(g, v) for (g, v) in fc_map.items() if v is not None]
					candidates.sort(key=lambda x: float(x[1]))  # 按 fc 升序
					for g, v in candidates:
						if float(v) >= required_fc:
							min_concrete_grade = g
							break
			except Exception:
				min_concrete_grade = ""

			# 钢筋数量（当前、最少）
			rebar_count_now = _parse_rebar_count_from_type(pile_type)
			min_rebar_count = 0  # TODO: 之后补充最少钢筋数量计算的逻辑
			# 描述：基于 当前混凝土强度 与 最低混凝土强度 的关系生成说明（并标注数值）
			desc_text = ""
			try:
				right_num = float(right_value) if right_value != "" else None
				current_fc_val = float(fc_val) if fc_val is not None else None
				min_fc_val = float(fc_map.get(min_concrete_grade)) if (min_concrete_grade and fc_map.get(min_concrete_grade) is not None) else None
				if (N_val is not None) and (right_num is not None) and (current_fc_val is not None) and (min_fc_val is not None):
					right_fmt = f"{right_num:.2f}"
					N_fmt = f"{N_val}"  # N 可直接输出，若需保留小数可调整格式
					if current_fc_val < min_fc_val:
						desc_text = f"该类桩轴心受压极值（{right_fmt} N）远小于荷载效应基本组合下的桩顶轴向压力设计值（{N_fmt} N），需将混凝土强度从（{current_concrete_grade}）提升至（{min_concrete_grade}）"
					elif current_fc_val == min_fc_val:
						desc_text = f"该类桩轴心受压极值（{right_fmt} N）贴近于荷载效应基本组合下的桩顶轴向压力设计值（{N_fmt} N），保持现有混凝土强度（{current_concrete_grade}）"
					else:
						desc_text = f"该类桩轴心受压极值（{right_fmt} N）远大于荷载效应基本组合下的桩顶轴向压力设计值（{N_fmt} N），可将混凝土强度从（{current_concrete_grade}）降低至（{min_concrete_grade}）"
			except Exception:
				desc_text = ""
			legend = (row.get(legend_col) or "").strip() if legend_col else ""
			rows_out.append({
				"N(N)": n_in_N,
				"phi": "0.75",
				"fc(N/mm^2)": ("" if fc_val is None else f"{fc_val}"),
				"Ap(mm^2)": ("" if Ap is None else f"{Ap}"),
				"右式": right_value,
				"目前混凝土强度": current_concrete_grade,
				"最低混凝土强度": min_concrete_grade,
				"目前钢筋数量": ("" if rebar_count_now is None else f"{rebar_count_now}"),
				"最少钢筋数量": f"{min_rebar_count}",
				"未注明的选用桩型": pile_type,
				"桩图例": legend,
				"描述": desc_text,
			})

	output_csv.parent.mkdir(parents=True, exist_ok=True)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out:
		fieldnames = [
			"N(N)",
			"phi",
			"fc(N/mm^2)",
			"Ap(mm^2)",
			"右式",
			"目前混凝土强度",
			"最低混凝土强度",
			"目前钢筋数量",
			"最少钢筋数量",
			"未注明的选用桩型",
			"桩图例",
			"描述",
		]
		writer = csv.DictWriter(f_out, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows_out)
	return len(rows_out)


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description="分析抗压桩：输出 N, phi, fc, Ap")
	parser.add_argument(
		"--input",
		type=Path,
		default=Path("dxf_tables") / "table_Model_grid_1.csv",
		help="输入CSV路径，默认为 dxf_tables/table_Model_grid_1.csv",
	)
	parser.add_argument(
		"--concrete",
		type=Path,
		default=Path("pre_rule") / "混凝土强度.json",
		help="混凝土强度参数JSON，默认为 pre_rule/混凝土强度.json",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=Path("outputs") / "抗压桩_计算参数.csv",
		help="输出CSV路径，默认为 outputs/抗压桩_计算参数.csv",
	)
	args = parser.parse_args(argv)

	if not args.input.exists():
		print(f"输入文件不存在: {args.input}", file=sys.stderr)
		return 2
	if not args.concrete.exists():
		print(f"混凝土参数文件不存在: {args.concrete}", file=sys.stderr)
		return 2

	try:
		count = extract_compression_piles(args.input, args.output, args.concrete)
		print(f"已写出 {count} 条抗压桩记录到: {args.output}")
		return 0
	except Exception as exc:
		print(f"处理失败: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main()) 