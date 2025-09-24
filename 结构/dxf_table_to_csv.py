from __future__ import annotations

import math
import csv
from pathlib import Path
from typing import Dict, List, Tuple, Iterable, Optional, Set

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFEntity, Line, LWPolyline, Text, MText, Insert

# ------------------------- User-configurable parameters -------------------------
# 用户可直接修改以下参数
BASE_DIR = Path(__file__).resolve().parent
INPUT_DXF = BASE_DIR / "dxf_tables" / "table_Model_grid_1.dxf"
OUTPUT_CSV = BASE_DIR / "dxf_tables" / "table_Model_grid_1.csv"
# 判断水平/竖直线以及坐标聚类的容差（图纸单位，一般mm）
AXIS_TOL = 0.5
# 坐标归并容差（将非常接近的坐标视为同一条网格线）
MERGE_TOL = 1.0
# 判断边界线是否存在的最小覆盖比例（相对于单元格边长）
EDGE_COVER_RATIO = 0.6

# --------------------------------------------------------------------------------


def nearly_equal(a: float, b: float, tol: float) -> bool:
	return abs(a - b) <= tol


def dedupe_sorted(values: List[float], tol: float) -> List[float]:
	if not values:
		return []
	values.sort()
	merged = [values[0]]
	for v in values[1:]:
		if abs(v - merged[-1]) > tol:
			merged.append(v)
	return merged


def overlap_length(a1: float, a2: float, b1: float, b2: float) -> float:
	lo = max(min(a1, a2), min(b1, b2))
	hi = min(max(a1, a2), max(b1, b2))
	return max(0.0, hi - lo)


class Grid:
	"""Represent base grid lines and provide edge presence queries."""

	def __init__(self, xs: List[float], ys_desc: List[float]):
		self.xs = xs  # ascending
		self.ys_desc = ys_desc  # descending (top -> bottom)
		self.rows = len(ys_desc) - 1
		self.cols = len(xs) - 1
		# Edge presence maps
		# vertical_edges[(i, j)] represents edge between cell(i-1) and cell(i) at x=xs[i], span y[j]..y[j+1]
		self.vertical_edges: Set[Tuple[int, int]] = set()
		# horizontal_edges[(i, j)] represents edge between cell(j-1) and cell(j) at y=ys_desc[j], span x[i]..x[i+1]
		self.horizontal_edges: Set[Tuple[int, int]] = set()

	def rect(self, col: int, row: int) -> Tuple[float, float, float, float]:
		# return (xmin, xmax, y_bottom, y_top)
		xmin = self.xs[col]
		xmax = self.xs[col + 1]
		y_top = self.ys_desc[row]
		y_bottom = self.ys_desc[row + 1]
		return xmin, xmax, y_bottom, y_top

	def point_to_cell(self, x: float, y: float) -> Optional[Tuple[int, int]]:
		# y is WCS; rows are top->bottom; find j such that y in (ys_desc[j+1], ys_desc[j]]
		# Use binary search
		# outside table -> None
		if x < self.xs[0] - MERGE_TOL or x > self.xs[-1] + MERGE_TOL:
			return None
		if y < self.ys_desc[-1] - MERGE_TOL or y > self.ys_desc[0] + MERGE_TOL:
			return None
		# find col
		lo, hi = 0, len(self.xs) - 1
		while lo < hi - 1:
			mid = (lo + hi) // 2
			if x >= self.xs[mid]:
				lo = mid
			else:
				hi = mid
		col = max(0, min(lo, self.cols - 1))
		# find row: ys_desc descending
		lo, hi = 0, len(self.ys_desc) - 1
		while lo < hi - 1:
			mid = (lo + hi) // 2
			if y <= self.ys_desc[mid]:
				lo = mid
			else:
				hi = mid
		row = max(0, min(lo, self.rows - 1))
		return col, row

	def neighbors_if_no_edge(self, col: int, row: int) -> Iterable[Tuple[int, int]]:
		# right neighbor: check vertical edge at i = col+1, between row span j=row
		if col + 1 < self.cols:
			edge_key = (col + 1, row)
			if edge_key not in self.vertical_edges:
				yield (col + 1, row)
		# left neighbor
		if col - 1 >= 0:
			edge_key = (col, row)
			if edge_key not in self.vertical_edges:
				yield (col - 1, row)
		# bottom neighbor: check horizontal edge at j = row+1
		if row + 1 < self.rows:
			edge_key = (col, row + 1)
			if edge_key not in self.horizontal_edges:
				yield (col, row + 1)
		# top neighbor
		if row - 1 >= 0:
			edge_key = (col, row)
			if edge_key not in self.horizontal_edges:
				yield (col, row - 1)


def collect_grid_lines(msp) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
	"""Return vertical and horizontal line segments.
	Verticals: (x, y1, y2); Horizontals: (y, x1, x2)
	"""
	verticals: List[Tuple[float, float, float]] = []
	horizontals: List[Tuple[float, float, float]] = []

	def add_line_segment(x1: float, y1: float, x2: float, y2: float) -> None:
		dx = x2 - x1
		dy = y2 - y1
		if abs(dx) <= AXIS_TOL and abs(dy) > AXIS_TOL:
			# vertical
			x = (x1 + x2) * 0.5
			y1_, y2_ = sorted((y1, y2))
			verticals.append((x, y1_, y2_))
		elif abs(dy) <= AXIS_TOL and abs(dx) > AXIS_TOL:
			# horizontal
			y = (y1 + y2) * 0.5
			x1_, x2_ = sorted((x1, x2))
			horizontals.append((y, x1_, x2_))
		else:
			# diagonal or tiny; ignore (用于斜线格子留空)
			return

	for e in msp.query("LINE LWPOLYLINE"):
		if isinstance(e, Line):
			p1 = e.dxf.start
			p2 = e.dxf.end
			add_line_segment(p1.x, p1.y, p2.x, p2.y)
		elif isinstance(e, LWPolyline):
			points = [tuple(p) for p in e.get_points(format="xy")]
			for (x1, y1), (x2, y2) in zip(points, points[1:]):
				add_line_segment(x1, y1, x2, y2)

	return verticals, horizontals


def build_grid(verticals: List[Tuple[float, float, float]],
			   horizontals: List[Tuple[float, float, float]]) -> Grid:
	# Collect unique x and y positions
	x_values = [v[0] for v in verticals]
	y_values = [h[0] for h in horizontals]
	if not x_values or not y_values:
		raise RuntimeError("未能在DXF中检测到表格网格线。请检查图层或实体类型。")
	xs = dedupe_sorted(x_values, MERGE_TOL)
	# y: top->bottom use descending order
	ys = dedupe_sorted(y_values, MERGE_TOL)
	ys_desc = list(reversed(ys))
	grid = Grid(xs, ys_desc)

	# Index segments for edge presence lookup
	vert_by_x: Dict[int, List[Tuple[float, float]]] = {}
	for x, y1, y2 in verticals:
		# find nearest xs index
		i = min(range(len(xs)), key=lambda k: abs(xs[k] - x))
		vert_by_x.setdefault(i, []).append((y1, y2))

	horz_by_y: Dict[int, List[Tuple[float, float]]] = {}
	for y, x1, x2 in horizontals:
		j_desc = min(range(len(ys_desc)), key=lambda k: abs(ys_desc[k] - y))
		horz_by_y.setdefault(j_desc, []).append((x1, x2))

	# Determine where edges actually exist per cell span
	for i in range(1, len(xs) - 0):
		# vertical edge index i at x=xs[i]
		segments = vert_by_x.get(i, [])
		for j in range(grid.rows):
			cell_yb = grid.ys_desc[j + 1]
			cell_yt = grid.ys_desc[j]
			span = cell_yt - cell_yb
			present = False
			for y1, y2 in segments:
				ov = overlap_length(y1, y2, cell_yb, cell_yt)
				if ov >= EDGE_COVER_RATIO * max(AXIS_TOL, span):
					present = True
					break
			if present:
				grid.vertical_edges.add((i, j))

	for j in range(1, len(ys_desc) - 0):
		# horizontal edge index j at y=ys_desc[j]
		segments = horz_by_y.get(j, [])
		for i in range(grid.cols):
			cell_xl = grid.xs[i]
			cell_xr = grid.xs[i + 1]
			span = cell_xr - cell_xl
			present = False
			for x1, x2 in segments:
				ov = overlap_length(x1, x2, cell_xl, cell_xr)
				if ov >= EDGE_COVER_RATIO * max(AXIS_TOL, span):
					present = True
					break
			if present:
				grid.horizontal_edges.add((i, j))

	return grid


def entity_center(e: DXFEntity) -> Optional[Tuple[float, float]]:
	try:
		bb = bbox.extents([e])
		if bb.has_data:
			c = bb.center
			return (float(c.x), float(c.y))
	except Exception:
		pass
	# Fallbacks
	try:
		if isinstance(e, Text):
			p = e.dxf.insert
			return (float(p.x), float(p.y))
		if isinstance(e, MText):
			p = e.dxf.insert
			return (float(p.x), float(p.y))
		if isinstance(e, Insert):
			p = e.dxf.insert
			return (float(p.x), float(p.y))
	except Exception:
		return None
	return None


def entity_text(e: DXFEntity) -> Optional[str]:
	if isinstance(e, Text):
		return (e.dxf.text or "").strip()
	if isinstance(e, MText):
		# 去除格式代码
		return (e.plain_text(split=False) or "").strip()
	if isinstance(e, Insert):
		# 仅写块名称
		try:
			return (e.dxf.name or "").strip()
		except Exception:
			return ""
	return None


def collect_cell_contents(msp, grid: Grid) -> Dict[Tuple[int, int], List[str]]:
	contents: Dict[Tuple[int, int], List[str]] = {}
	for e in msp:
		type_ = e.dxftype()
		if type_ not in ("TEXT", "MTEXT", "INSERT"):
			continue
		c = entity_center(e)
		if c is None:
			continue
		x, y = c
		cell = grid.point_to_cell(x, y)
		if cell is None:
			continue
		text = entity_text(e)
		if text is None:
			continue
		text = text.strip()
		if not text:
			continue
		# 内容放入该单元格，合并复制在后续区域扩张时处理
		contents.setdefault(cell, []).append(text)
	return contents


def build_regions(grid: Grid) -> List[List[Tuple[int, int]]]:
	visited = [[False] * grid.cols for _ in range(grid.rows)]
	regions: List[List[Tuple[int, int]]] = []
	from collections import deque
	for row in range(grid.rows):
		for col in range(grid.cols):
			if visited[row][col]:
				continue
			# BFS across open edges
			q = deque([(col, row)])
			visited[row][col] = True
			cells: List[Tuple[int, int]] = []
			while q:
				c0, r0 = q.popleft()
				cells.append((c0, r0))
				for nc, nr in grid.neighbors_if_no_edge(c0, r0):
					if not visited[nr][nc]:
						visited[nr][nc] = True
						q.append((nc, nr))
			regions.append(cells)
	return regions


def write_csv(grid: Grid, contents: Dict[Tuple[int, int], List[str]], out_path: Path) -> None:
	# Expand contents across merged regions
	regions = build_regions(grid)
	region_content: Dict[Tuple[int, int], str] = {}
	for cells in regions:
		texts: List[str] = []
		for c in cells:
			texts.extend(contents.get(c, []))
		joined = " ".join(dict.fromkeys([t for t in (s.strip() for s in texts) if t]))  # 去重保持顺序
		for c in cells:
			region_content[c] = joined

	# Build matrix rows x cols
	matrix: List[List[str]] = []
	for row in range(grid.rows):
		row_vals: List[str] = []
		for col in range(grid.cols):
			val = region_content.get((col, row), "")
			row_vals.append(val)
		matrix.append(row_vals)

	out_path.parent.mkdir(parents=True, exist_ok=True)
	with out_path.open("w", newline="", encoding="utf-8-sig") as f:
		writer = csv.writer(f)
		for r in matrix:
			writer.writerow(r)


def main() -> None:
	if not INPUT_DXF.exists():
		raise SystemExit(f"找不到输入DXF: {INPUT_DXF}")
	print(f"读取: {INPUT_DXF}")
	doc = ezdxf.readfile(str(INPUT_DXF))
	msp = doc.modelspace()
	print("收集表格线...")
	verticals, horizontals = collect_grid_lines(msp)
	print(f"竖线段: {len(verticals)}, 横线段: {len(horizontals)}")
	grid = build_grid(verticals, horizontals)
	print(f"网格: {grid.rows} 行 x {grid.cols} 列")
	print("收集单元格内容...")
	contents = collect_cell_contents(msp, grid)
	print(f"含内容的初始单元格数: {len(contents)}")
	print(f"导出CSV: {OUTPUT_CSV}")
	write_csv(grid, contents, OUTPUT_CSV)
	print("完成。")


if __name__ == "__main__":
	main() 