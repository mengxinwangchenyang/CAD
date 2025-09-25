#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Optional


def detect_encoding(path: Path) -> str:
	return "utf-8-sig"


def find_field(fieldnames, keyword: str) -> Optional[str]:
	if not fieldnames:
		return None
	for name in fieldnames:
		if keyword in (name or ""):
			return name
	return None


def build_legend_to_row_index(input_csv: Path) -> Dict[str, int]:
	mapping: Dict[str, int] = {}
	with input_csv.open("r", encoding=detect_encoding(input_csv), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			raise ValueError("CSV缺少表头")
		legend_col = find_field(reader.fieldnames, "桩图例") or find_field(reader.fieldnames, "ModuleName")
		if legend_col is None:
			raise KeyError("未找到列：桩图例 或 ModuleName")
		row_idx = 0
		for row in reader:
			# 跳过空行
			if not any((v or "").strip() for v in row.values() if v is not None):
				continue
			row_idx += 1
			legend = (row.get(legend_col) or "").strip()
			if legend and legend not in mapping:
				mapping[legend] = row_idx
	return mapping


def main() -> int:
	parser = argparse.ArgumentParser(description="根据 table_Model_grid_1.csv 生成 桩图例->行号 JSON 映射（1-based）")
	parser.add_argument("--input", type=Path, default=Path("dxf_tables") / "table_Model_grid_1.csv", help="输入CSV，默认 dxf_tables/table_Model_grid_1.csv")
	parser.add_argument("--output", type=Path, default=Path("outputs") / "桩图例_to_row_index.json", help="输出JSON，默认 outputs/桩图例_to_row_index.json")
	args = parser.parse_args()

	if not args.input.exists():
		print(f"输入文件不存在: {args.input}")
		return 2
	args.output.parent.mkdir(parents=True, exist_ok=True)
	mapping = build_legend_to_row_index(args.input)
	with args.output.open("w", encoding="utf-8") as jf:
		json.dump(mapping, jf, ensure_ascii=False, indent=2)
	print(f"已写出 {len(mapping)} 条映射到: {args.output}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main()) 