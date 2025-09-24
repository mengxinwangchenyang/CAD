#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

# 配置：dwg2dxf.exe 所在目录（或其上级含 bin）。可在命令行传入覆盖。
LIBREDWG_DIR = os.environ.get("LIBREDWG_DIR", r"C:\\Users\\30473\\Desktop\\研一\\cad\\libredwg-0.13.3.7825-win64")


def _find_dwg2dxf(exe_root: Path) -> Path:
	candidates = [
		exe_root / "dwg2dxf.exe",
		exe_root / "bin" / "dwg2dxf.exe",
	]
	for p in candidates:
		if p.exists():
			return p
	raise FileNotFoundError(f"dwg2dxf.exe not found under {exe_root}")


def convert_dwg_to_dxf(dwg_path: Path, dxf_path: Optional[Path] = None, libredwg_dir: Optional[Path] = None) -> Path:
	"""Convert a DWG file to DXF using LibreDWG's dwg2dxf.exe.

	Parameters
	----------
	- dwg_path: Path to input .dwg
	- dxf_path: Output .dxf path. If None, use same directory/name with .dxf suffix
	- libredwg_dir: Directory containing dwg2dxf.exe (or its bin/). If None, use LIBREDWG_DIR

	Returns
	-------
	Path to the resulting .dxf
	"""
	dwg_path = Path(dwg_path)
	if dxf_path is None:
		dxf_path = dwg_path.with_suffix(".dxf")
	else:
		dxf_path = Path(dxf_path)

	if not dwg_path.exists():
		raise FileNotFoundError(f"Input DWG not found: {dwg_path}")

	exe_root = Path(libredwg_dir or LIBREDWG_DIR)
	exe = _find_dwg2dxf(exe_root)

	# 确保输出目录
	dxf_path.parent.mkdir(parents=True, exist_ok=True)

	# 运行 dwg2dxf
	cmd = [str(exe), str(dwg_path)]
	print(f"[RUN ] {' '.join(cmd)}")
	proc = subprocess.run(cmd, capture_output=True, text=True)
	if proc.stdout:
		print(proc.stdout, end="")
	if proc.stderr:
		print(proc.stderr, end="")
	if proc.returncode != 0:
		raise RuntimeError(f"dwg2dxf failed (exit {proc.returncode}) for {dwg_path}")

	# 期望输出与输入同目录同名 .dxf；若不在，尝试若干候选位置并搬运
	candidates = [
		dwg_path.with_suffix(".dxf"),
		exe.parent / (dwg_path.stem + ".dxf"),
		Path.cwd() / (dwg_path.stem + ".dxf"),
	]
	found: Optional[Path] = None
	for c in candidates:
		if c.exists():
			found = c
			break
	if found is None:
		raise RuntimeError(f"dwg2dxf produced no output for {dwg_path}")

	# 搬运到目标路径
	try:
		if found.resolve() != dxf_path.resolve():
			# 覆盖到目标位置
			if dxf_path.exists():
				dxf_path.unlink()
			found.replace(dxf_path)
	except Exception as e:
		raise RuntimeError(f"Failed moving {found} -> {dxf_path}: {e}")

	print(f"[OK  ] {dxf_path}")
	return dxf_path


def main(argv: Optional[list[str]] = None) -> int:
	import argparse
	parser = argparse.ArgumentParser(description="Convert DWG to DXF using LibreDWG dwg2dxf.exe")
	parser.add_argument("input", type=str, help="Input .dwg path")
	parser.add_argument("--out", type=str, default=None, help="Output .dxf path (optional)")
	parser.add_argument("--lib", type=str, default=None, help="LibreDWG dir containing dwg2dxf.exe or its bin/")
	args = parser.parse_args(argv)

	try:
		convert_dwg_to_dxf(Path(args.input), Path(args.out) if args.out else None, Path(args.lib) if args.lib else None)
		return 0
	except Exception as e:
		print(f"[ERROR] {e}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main()) 