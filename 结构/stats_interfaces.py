#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional


BASE_DIR = Path(".")
OUTPUTS = BASE_DIR / "outputs"
TABLES = BASE_DIR / "dxf_tables"
LEGEND_MAP_JSON = OUTPUTS / "桩图例_to_row_index.json"


def _detect_encoding(path: Path) -> str:
	return "utf-8-sig"


def _find_field(fieldnames: List[str], keyword: str) -> Optional[str]:
	if not fieldnames:
		return None
	for name in fieldnames:
		if keyword in (name or ""):
			return name
	return None


def _load_legend_index_map() -> Dict[str, int]:
	try:
		with LEGEND_MAP_JSON.open("r", encoding="utf-8") as f:
			return json.load(f)
	except Exception:
		return {}


def get_compression_piles_json(csv_path: Path = OUTPUTS / "抗压桩_计算参数.csv") -> List[Dict[str, object]]:
	legend_map = _load_legend_index_map()
	rows_out: List[Dict[str, object]] = []
	with csv_path.open("r", encoding=_detect_encoding(csv_path), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			return rows_out
		legend_col = _find_field(reader.fieldnames, "桩图例") or _find_field(reader.fieldnames, "ModuleName")
		type_col = _find_field(reader.fieldnames, "未注明的选用桩型")
		n_col = _find_field(reader.fieldnames, "N(N)")
		right_col = _find_field(reader.fieldnames, "右式")
		cur_fc_col = _find_field(reader.fieldnames, "目前混凝土强度")
		min_fc_col = _find_field(reader.fieldnames, "最低混凝土强度")
		desc_col = _find_field(reader.fieldnames, "描述")
		for row in reader:
			legend_txt = (row.get(legend_col) or "").strip() if legend_col else ""
			legend_idx = legend_map.get(legend_txt)
			rows_out.append({
				"桩图例": legend_idx,
				"桩型": (row.get(type_col) or "").strip() if type_col else "",
				"单桩顶轴向压力设计值": (row.get(n_col) or "").strip() if n_col else "",
				"单桩轴心受压极值": (row.get(right_col) or "").strip() if right_col else "",
				"混凝土强度": (row.get(cur_fc_col) or "").strip() if cur_fc_col else "",
				"最低可优化混凝土强度": (row.get(min_fc_col) or "").strip() if min_fc_col else "",
				"描述": (row.get(desc_col) or "").strip() if desc_col else "",
			})
	return rows_out


def get_uplift_piles_json(csv_path: Path = OUTPUTS / "抗拔桩_参数计算.csv") -> List[Dict[str, object]]:
	legend_map = _load_legend_index_map()
	rows_out: List[Dict[str, object]] = []
	with csv_path.open("r", encoding=_detect_encoding(csv_path), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			return rows_out
		legend_col = _find_field(reader.fieldnames, "桩图例") or _find_field(reader.fieldnames, "ModuleName")
		type_col = _find_field(reader.fieldnames, "未注明的选用桩型")
		Nk_col = _find_field(reader.fieldnames, "Nk(N)")
		right_col = _find_field(reader.fieldnames, "右式")
		w_col = _find_field(reader.fieldnames, "w")
		count_col = _find_field(reader.fieldnames, "目前钢筋数量")
		deq_col = _find_field(reader.fieldnames, "deq(mm)")
		opt_count_col = _find_field(reader.fieldnames, "优化后钢筋数量")
		desc_col = _find_field(reader.fieldnames, "描述")
		for row in reader:
			legend_txt = (row.get(legend_col) or "").strip() if legend_col else ""
			legend_idx = legend_map.get(legend_txt)
			deq_val = (row.get(deq_col) or "").strip() if deq_col else ""
			rows_out.append({
				"桩图例": legend_idx,
				"桩型": (row.get(type_col) or "").strip() if type_col else "",
				"最大承载抗拔力": (row.get(Nk_col) or "").strip() if Nk_col else "",
				"桩身最大裂缝宽度": (row.get(right_col) or "").strip() if right_col else "",
				"最大裂缝宽度限值": (row.get(w_col) or "").strip() if w_col else "",
				"钢筋数量": (row.get(count_col) or "").strip() if count_col else "",
				"钢筋直径": deq_val,
				"优化后钢筋数量": (row.get(opt_count_col) or "").strip() if opt_count_col else "",
				"优化后钢筋直径": deq_val,
				"描述": (row.get(desc_col) or "").strip() if desc_col else "",
			})
	return rows_out


def get_zoned_uplift_json(csv_path: Path = OUTPUTS / "抗拔桩_分区计算.csv") -> List[Dict[str, object]]:
	legend_map = _load_legend_index_map()
	rows_out: List[Dict[str, object]] = []
	with csv_path.open("r", encoding=_detect_encoding(csv_path), newline="") as f:
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			return rows_out
		# columns
		zone_val_col = _find_field(reader.fieldnames, "区域承载力比值")
		zone_color_col = _find_field(reader.fieldnames, "区域桩颜色")
		zone_count_col = _find_field(reader.fieldnames, "区域桩数量")
		zone_total_col = _find_field(reader.fieldnames, "区域总配筋数")
		zone_opt_col = _find_field(reader.fieldnames, "区域可优化配筋数")
		zone_avg_before_col = _find_field(reader.fieldnames, "优化前桩均钢筋数")
		zone_avg_after_col = _find_field(reader.fieldnames, "优化后桩均钢筋数")
		zone_desc_col = _find_field(reader.fieldnames, "区域描述")
		legend_col = _find_field(reader.fieldnames, "桩图例") or _find_field(reader.fieldnames, "ModuleName")
		pile_count_col = _find_field(reader.fieldnames, "桩数量")
		pre_cnt_col = _find_field(reader.fieldnames, "优化前钢筋数")
		post_cnt_col = _find_field(reader.fieldnames, "优化后钢筋数")
		pre_dia_col = _find_field(reader.fieldnames, "优化前钢筋直径")
		post_dia_col = _find_field(reader.fieldnames, "优化后钢筋直径")
		pile_desc_col = _find_field(reader.fieldnames, "描述")
		# zone index mapping
		zone_order = {"<0.3": "区域1", "0.3-0.6": "区域2", ">0.6": "区域3"}
		for row in reader:
			zname = (row.get(zone_val_col) or "").strip() if zone_val_col else ""
			zidx = zone_order.get(zname)
			legend_txt = (row.get(legend_col) or "").strip() if legend_col else ""
			legend_idx = legend_map.get(legend_txt)
			rows_out.append({
				"区域序号": zidx,
				"区域承载力比值": zname,
				"区域桩颜色": (row.get(zone_color_col) or "").strip() if zone_color_col else "",
				"区域桩数量": (row.get(zone_count_col) or "").strip() if zone_count_col else "",
				"区域总配筋数": (row.get(zone_total_col) or "").strip() if zone_total_col else "",
				"可优化配筋数": (row.get(zone_opt_col) or "").strip() if zone_opt_col else "",
				"优化前桩均钢筋数": (row.get(zone_avg_before_col) or "").strip() if zone_avg_before_col else "",
				"优化后桩均钢筋数": (row.get(zone_avg_after_col) or "").strip() if zone_avg_after_col else "",
				"区域描述": (row.get(zone_desc_col) or "").strip() if zone_desc_col else "",
				"桩图例": legend_idx,
				"桩数量": (row.get(pile_count_col) or "").strip() if pile_count_col else "",
				"优化前钢筋数": (row.get(pre_cnt_col) or "").strip() if pre_cnt_col else "",
				"优化后钢筋数": (row.get(post_cnt_col) or "").strip() if post_cnt_col else "",
				"优化前钢筋直径": (row.get(pre_dia_col) or "").strip() if pre_dia_col else "",
				"优化后钢筋直径": (row.get(post_dia_col) or "").strip() if post_dia_col else "",
				"描述": (row.get(pile_desc_col) or "").strip() if pile_desc_col else "",
			})
	return rows_out


if __name__ == "__main__":
	print(get_compression_piles_json())
	print(get_uplift_piles_json())
	print(get_zoned_uplift_json())