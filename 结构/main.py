#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
from pathlib import Path
from typing import Optional
import json

# Step modules
from dwg_to_dxf import convert_dwg_to_dxf
from dxf2csv import export_dxf_to_csv
import extract_dxf_table
import dxf_table_to_csv
from build_legend_row_index import build_legend_to_row_index
import annotate_union
import annotate_uplift_force
import annotate_pile_types
import analyze_zoned_uplift_piles
import colorize_piles_by_ratio
import analyze_uplift_piles
import analyze_compression_piles


def _print_step(title: str) -> None:
	print("\n" + "=" * 80)
	print(f"[STEP] {title}")
	print("=" * 80)


def main(argv: Optional[list[str]] = None) -> int:
	import argparse
	parser = argparse.ArgumentParser(description="Run CAD pipeline end-to-end with two DWG inputs")
	parser.add_argument("pile_dwg",nargs='?', type=str,default="桩位图.dwg", help="桩位图 DWG 文件路径")
	parser.add_argument("uplift_dwg", nargs='?', type=str,default="原设计桩基抗拔反力.dwg", help="原设计桩基抗拔反力 DWG 文件路径")
	parser.add_argument("--lib", type=str, default=None, help="LibreDWG 安装目录，包含 dwg2dxf.exe 或其 bin/")
	parser.add_argument("--skip-colorize", action="store_true", help="跳过按承载力比值分层上色的最终DXF导出")
	args = parser.parse_args(argv)

	cwd = Path.cwd()
	outputs_dir = cwd / "outputs"
	outputs_dir.mkdir(parents=True, exist_ok=True)

	pile_dwg_path = Path(args.pile_dwg).resolve()
	uplift_dwg_path = Path(args.uplift_dwg).resolve()
	if not pile_dwg_path.exists():
		print(f"[ERROR] 桩位图 DWG 不存在: {pile_dwg_path}", file=sys.stderr)
		return 2
	if not uplift_dwg_path.exists():
		print(f"[ERROR] 原设计桩基抗拔反力 DWG 不存在: {uplift_dwg_path}", file=sys.stderr)
		return 2

	# 统一输出文件名，便于后续脚本使用其内置常量
	pile_dxf = cwd / "桩位图.dxf"
	uplift_dxf = cwd / "原设计桩基抗拔反力.dxf"

	try:
		_print_step("将 DWG 转为 DXF")
		if pile_dxf.exists():
			print(f"[SKIP] 已存在: {pile_dxf}")
		else:
			convert_dwg_to_dxf(pile_dwg_path, pile_dxf, Path(args.lib) if args.lib else None)
		if uplift_dxf.exists():
			print(f"[SKIP] 已存在: {uplift_dxf}")
		else:
			convert_dwg_to_dxf(uplift_dwg_path, uplift_dxf, Path(args.lib) if args.lib else None)
	except Exception as exc:
		print(f"[FATAL] DWG 转 DXF 失败: {exc}", file=sys.stderr)
		return 1

	try:
		_print_step("提取表格片段到 dxf_tables/")
		extract_dxf_table.main()
	except SystemExit as e:
		if int(e.code) != 0:
			return int(e.code)
	except Exception as exc:
		print(f"[FATAL] 提取表格失败: {exc}", file=sys.stderr)
		return 1

	# 将第一张表格DXF转换为CSV（默认使用 table_Model_grid_1）
	try:
		_print_step("将表格DXF转换为CSV")
		table_csv = cwd / "dxf_tables" / "table_Model_grid_1.csv"
		if table_csv.exists():
			print(f"[SKIP] 已存在: {table_csv}")
		else:
			dxf_table_to_csv.main()
	except SystemExit as e:
		if int(e.code) != 0:
			return int(e.code)
	except Exception as exc:
		print(f"[FATAL] 表格DXF转CSV失败: {exc}", file=sys.stderr)
		return 1

	# 生成 桩图例->行号 映射 JSON
	try:
		_print_step("生成 桩图例->行号 映射 JSON")
		legend_map = build_legend_to_row_index(cwd / "dxf_tables" / "table_Model_grid_1.csv")
		legend_json = outputs_dir / "桩图例_to_row_index.json"
		legend_json.parent.mkdir(parents=True, exist_ok=True)
		legend_json.write_text(json.dumps(legend_map, ensure_ascii=False, indent=2), encoding="utf-8")
		print(f"[OK  ] 写出映射 {legend_json}")
	except Exception as exc:
		print(f"[WARN] 生成桩图例映射失败：{exc}")
		return 1

	# 从原设计抗拔反力 DXF 导出 CSV（供后续叠加标注）
	try:
		_print_step("从原设计抗拔反力DXF导出CSV")
		uplift_csv = outputs_dir / "原设计桩基抗拔反力.csv"
		if uplift_csv.exists():
			print(f"[SKIP] 已存在: {uplift_csv}")
		else:
			export_dxf_to_csv(uplift_dxf, uplift_csv)
	except Exception as exc:
		print(f"[FATAL] 导出原设计抗拔反力CSV失败: {exc}", file=sys.stderr)
		return 1

	# 根据原设计抗拔反力CSV，向桩位图叠加文字，得到 桩位图_抗拔反力_标注.dxf
	try:
		_print_step("叠加抗拔反力标注到桩位图DXF")
		annotate_union.main()
	except SystemExit as e:
		if int(e.code) != 0:
			return int(e.code)
	except Exception as exc:
		print(f"[FATAL] 叠加抗拔反力标注失败: {exc}", file=sys.stderr)
		return 1

	# 将 桩位图_抗拔反力_标注.dxf 导出为 CSV，供下一步识别最近反力文本
	try:
		_print_step("导出 桩位图_抗拔反力_标注.dxf 为 CSV")
		annot_csv = outputs_dir / "桩位图_抗拔反力_标注.csv"
		annot_dxf_in_outputs = outputs_dir / "桩位图_抗拔反力_标注.dxf"
		if not annot_dxf_in_outputs.exists():
			print(f"[ERROR] 未找到标注DXF: {annot_dxf_in_outputs}", file=sys.stderr)
			return 1
		export_dxf_to_csv(annot_dxf_in_outputs, annot_csv)
	except Exception as exc:
		print(f"[FATAL] 导出桩位图抗拔反力标注CSV失败: {exc}", file=sys.stderr)
		return 1

	# 基于最近有效 TEXT反力，写出 _文字.dxf 与带“反力”列的CSV
	try:
		_print_step("生成 桩位图_抗拔反力_标注_文字.dxf 与带反力列的CSV")
		# 覆盖其输入DXF到 outputs 下的标注文件
		annotate_uplift_force.PATH_INPUT_DXF = str(outputs_dir / "桩位图_抗拔反力_标注.dxf")
		# 确保读取的CSV也指向 outputs 下
		annotate_uplift_force.PATH_PILE_CSV = str(outputs_dir / "桩位图_抗拔反力_标注.csv")
		annotate_uplift_force.main()
	except SystemExit as e:
		if int(e.code) != 0:
			return int(e.code)
	except Exception as exc:
		print(f"[FATAL] 生成反力文字标注失败: {exc}", file=sys.stderr)
		return 1

	# 基于表映射输出 ‘桩位图_图标标注.dxf’ 与增广 CSV（含选用桩型等表字段）
	try:
		_print_step("生成 桩位图_图标标注.dxf 与增广CSV")
		annotate_pile_types.main()
	except SystemExit as e:
		if int(e.code) != 0:
			return int(e.code)
	except Exception as exc:
		print(f"[FATAL] 生成图标标注失败: {exc}", file=sys.stderr)
		return 1

	# 计算承载力比值并输出最终CSV
	try:
		_print_step("计算承载力比值并输出最终CSV")
		ret = analyze_zoned_uplift_piles.main([])
		if ret != 0:
			return ret
	except Exception as exc:
		print(f"[FATAL] 计算承载力比值失败: {exc}", file=sys.stderr)
		return 1

	# 依据最终CSV对桩按分区复制到不同图层，得到最终上色DXF
	if not args.skip_colorize:
		try:
			_print_step("按承载力比值分层复制并导出DXF")
			ret2 = colorize_piles_by_ratio.process()
			if ret2 != 0:
				return ret2
		except Exception as exc:
			print(f"[FATAL] 上色导出失败: {exc}", file=sys.stderr)
			return 1

	# 输出抗拔/抗压参数CSV
	try:
		_print_step("计算抗拔/抗压参数CSV")
		an1 = analyze_uplift_piles.main([])
		an2 = analyze_compression_piles.main([])
		if an1 not in (0, None) or an2 not in (0, None):
			print("[FATAL] 参数计算存在非零退出码")
	except Exception as exc:
		print(f"[FATAL] 抗拔/抗压参数计算失败: {exc}", file=sys.stderr)
		return 1

	print("\n[OK] 全部流程完成。输出目录:", outputs_dir)
	return 0


if __name__ == "__main__":
	sys.exit(main()) 