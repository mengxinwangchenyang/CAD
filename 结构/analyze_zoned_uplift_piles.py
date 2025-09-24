#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional


INPUT_DEFAULT = Path("outputs") / "桩位图_图标标注.csv"
OUTPUT_DEFAULT = Path("outputs") / "桩位图_final.csv"


def detect_encoding(path: Path) -> str:
	return "utf-8-sig"


def find_field(fieldnames: List[str], keyword: str) -> Optional[str]:
	if not fieldnames:
		return None
	for name in fieldnames:
		if keyword in (name or ""):
			return name
	return None


def to_float(val: str) -> Optional[float]:
	try:
		v = (val or "").strip()
		if v == "":
			return None
		return float(v.replace(",", "").replace("kN", "").strip())
	except Exception:
		return None


def write_with_ratio(input_csv: Path, output_csv: Path) -> int:
	"""Write all rows with added 承载力比值 = |反力| / 单桩竖向抗拔承载力特征值 for uplift rows.

	If either value missing or zero, leave empty.
	"""
	with input_csv.open("r", encoding=detect_encoding(input_csv), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			raise ValueError("输入CSV缺少表头")
		uplift_col = find_field(reader.fieldnames, "单桩竖向抗拔承载力特征值")
		force_col = find_field(reader.fieldnames, "反力")
		if uplift_col is None or force_col is None:
			raise KeyError("未找到所需列：反力 或 单桩竖向抗拔承载力特征值")

		rows = []
		for row in reader:
			N2 = to_float(row.get(uplift_col) or "")
			N1 = to_float(row.get(force_col) or "")
			ratio = ""
			if N2 is not None and N2 != 0 and N1 is not None:
				ratio = f"{abs(N1) / N2}"
			row_out = dict(row)
			row_out["承载力比值"] = ratio
			rows.append(row_out)

	fieldnames = list(reader.fieldnames) + (["承载力比值"] if "承载力比值" not in reader.fieldnames else [])
	output_csv.parent.mkdir(parents=True, exist_ok=True)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out:
		writer = csv.DictWriter(f_out, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)
	return len(rows)


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description="计算承载力比值并输出最终桩位图CSV")
	parser.add_argument("--input", type=Path, default=INPUT_DEFAULT, help="输入CSV，默认 outputs/桩位图_图标标注.csv")
	parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT, help="输出CSV，默认 outputs/桩位图_final.csv")
	args = parser.parse_args(argv)

	if not args.input.exists():
		print(f"输入文件不存在: {args.input}", file=sys.stderr)
		return 2

	try:
		count = write_with_ratio(args.input, args.output)
		print(f"已写出 {count} 行到: {args.output}")
		return 0
	except Exception as exc:
		print(f"处理失败: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main()) 