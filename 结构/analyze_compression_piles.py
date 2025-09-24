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
	"""
	encoding = detect_encoding(input_csv)
	fc_map = load_concrete_fc(concrete_json)
	with input_csv.open("r", encoding=encoding, newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			raise ValueError("CSV appears to have no header row.")

		uplift_col = find_field(reader.fieldnames, "单桩竖向抗拔承载力特征值")
		compress_col = find_field(reader.fieldnames, "单桩竖向抗压承载力特征值")
		type_col = find_field(reader.fieldnames, "未注明的选用桩型")
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
			# 右式 = phi * fc * Ap (单位：N)
			right_value = ""
			try:
				if (fc_val is not None) and (Ap is not None):
					phi_val = 0.75
					right_value = f"{phi_val * float(fc_val) * float(Ap)}"
			except Exception:
				right_value = ""
			rows_out.append({
				"N(N)": n_in_N,
				"phi": "0.75",
				"fc(N/mm^2)": ("" if fc_val is None else f"{fc_val}"),
				"Ap(mm^2)": ("" if Ap is None else f"{Ap}"),
				"右式": right_value,
			})

	output_csv.parent.mkdir(parents=True, exist_ok=True)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out:
		fieldnames = ["N(N)", "phi", "fc(N/mm^2)", "Ap(mm^2)", "右式"]
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
		default=Path("outputs") / "抗压桩_N_phi_fc_Ap.csv",
		help="输出CSV路径，默认为 outputs/抗压桩_N_phi_fc_Ap.csv",
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