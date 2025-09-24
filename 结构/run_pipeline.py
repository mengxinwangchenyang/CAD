#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess
import time
from pathlib import Path

# Workspace root (this file should live alongside the other scripts)
ROOT = Path(__file__).resolve().parent
PY = sys.executable or "python"


def run_step(title: str, argv: list[str]) -> None:
	print(f"\n[STEP] {title}")
	print(f"[CMD ] {' '.join(argv)}")
	start = time.time()
	proc = subprocess.run(argv, cwd=str(ROOT))
	if proc.returncode != 0:
		raise SystemExit(f"[FAIL] {title} 退出码 {proc.returncode}")
	dur = time.time() - start
	print(f"[OK  ] {title} 用时 {dur:.1f}s")


def _resolve_dxf_path(p: Path) -> Path:
	"""If a .dwg is given, prefer same-name .dxf; if missing, auto-convert."""
	if p.suffix.lower() == ".dwg":
		alt = p.with_suffix(".dxf")
		if alt.exists():
			return alt
		# auto-convert DWG -> DXF
		from dwg_to_dxf import convert_dwg_to_dxf
		print(f"[INFO] 未找到同名DXF，开始转换: {p} -> {alt}")
		convert_dwg_to_dxf(p, alt)
		return alt
	return p


def run_export(title: str, in_path: Path, out_path: Path) -> None:
	from importlib import import_module
	print(f"\n[STEP] {title}")
	in_resolved = _resolve_dxf_path(in_path)
	print(f"[INFO] 输入: {in_resolved}")
	print(f"[INFO] 输出: {out_path}")
	start = time.time()
	try:
		mod = import_module("dxf2csv")
		out_path.parent.mkdir(parents=True, exist_ok=True)
		mod.export_dxf_to_csv(str(in_resolved), str(out_path))
	except Exception as exc:
		raise SystemExit(f"[FAIL] {title}: {exc}")
	dur = time.time() - start
	print(f"[OK  ] {title} 用时 {dur:.1f}s")


def main() -> int:
	# Ensure outputs dir exists
	(ROOT / "outputs").mkdir(exist_ok=True)

	# 0) 预先将两份原始图纸转为CSV，供后续步骤使用
	run_export(
		"转换 原设计桩基抗拔反力 到 CSV (dxf2csv.export_dxf_to_csv)",
		ROOT / "原设计桩基抗拔反力.dwg",
		ROOT / "outputs" / "原设计桩基抗拔反力.csv",
	)
	run_export(
		"转换 桩位图 到 CSV (dxf2csv.export_dxf_to_csv)",
		ROOT / "桩位图.dwg",
		ROOT / "outputs" / "桩位图.csv",
	)

	# 1) 将原始反力CSV标注到桩位图，输出: outputs/桩位图_抗拔反力_标注.dxf
	run_step(
		"标注原始反力到桩位图 (annotate_union.py)",
		[PY, str(ROOT / "annotate_union.py")],
	)

	# 2) 从标注后的桩位图导出CSV: outputs/桩位图_抗拔反力_标注.csv
	run_step(
		"从DXF导出桩位图CSV (dxf2csv.py)",
		[PY, str(ROOT / "dxf2csv.py"), "--out", str(ROOT / "outputs")],
	)

	# 3) 从导出的CSV与表格建立关联，生成含文字标注的DXF与CSV
	run_step(
		"生成含文字标注的桩位图与CSV (annotate_uplift_force.py)",
		[PY, str(ROOT / "annotate_uplift_force.py")],
	)

	# 4) 依据表格为桩添加“选用桩型”等标注，输出: 桩位图_图标标注.dxf 与 outputs/桩位图_图标标注.csv
	run_step(
		"生成图标标注与增广CSV (annotate_pile_types.py)",
		[PY, str(ROOT / "annotate_pile_types.py")],
	)

	# 5) 计算抗拔桩参数，输出: outputs/抗拔桩_参数计算.csv
	run_step(
		"计算抗拔桩参数 (analyze_uplift_piles.py)",
		[PY, str(ROOT / "analyze_uplift_piles.py")],
	)

	# 6) 由参数计算 x 结果，输出: outputs/抗拔桩_x_results.csv（使用默认 y 列表，可在脚本中调整）
	run_step(
		"计算抗拔桩 x 结果 (compute_num_from_N.py)",
		[PY, str(ROOT / "compute_num_from_N.py")],
	)

	# 7) 基于增广CSV计算承载力比值并输出最终桩位图CSV: outputs/桩位图_final.csv
	run_step(
		"计算承载力比值并生成最终CSV (analyze_zoned_uplift_piles.py)",
		[PY, str(ROOT / "analyze_zoned_uplift_piles.py")],
	)

	# 8) 按比值分层着色并输出最终DXF: outputs/桩位图_final.dxf
	run_step(
		"按承载力比值分层着色 (colorize_piles_by_ratio.py)",
		[PY, str(ROOT / "colorize_piles_by_ratio.py")],
	)

	# 9)（独立步骤，可选）分析抗压桩并输出: outputs/抗压桩_N_phi_fc_Ap.csv
	run_step(
		"分析抗压桩 (analyze_compression_piles.py)",
		[PY, str(ROOT / "analyze_compression_piles.py")],
	)

	print("\n[PIPELINE OK] 全部步骤完成。")
	return 0


if __name__ == "__main__":
	sys.exit(main()) 