#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional
import math
from compute_num_from_N import compute_x1_flexible

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
		# Zone counters per legend
		legend_col = find_field(reader.fieldnames, "桩图例") or find_field(reader.fieldnames, "ModuleName")
		module_id_col = find_field(reader.fieldnames, "ModuleNowID")
		type_col = find_field(reader.fieldnames, "未注明的选用桩型") or find_field(reader.fieldnames, "选用桩型")
		zone_counts: dict = {
			"<0.3": {},
			"0.3-0.6": {},
			">0.6": {},
		}
		# Keep one pile type text per (zone, legend)
		zone_legend_type: dict = {"<0.3": {}, "0.3-0.6": {}, ">0.6": {}}
		# Map legend -> first row_index in 抗拔桩_参数计算.csv
		legend_to_row_index: dict[str, int] = {}
		try:
			param_csv = Path("outputs") / "抗拔桩_参数计算.csv"
			with param_csv.open("r", encoding="utf-8-sig", newline="") as pf:
				pr = csv.DictReader(pf)
				name_col = find_field(pr.fieldnames or [], "桩图例") or find_field(pr.fieldnames or [], "ModuleName")
				idx = 0
				for prow in pr:
					# skip empty
					if not any((v or "").strip() for v in prow.values() if v is not None):
						continue
					idx += 1
					lg = (prow.get(name_col) or "").strip() if name_col else ""
					if lg and lg not in legend_to_row_index:
						legend_to_row_index[lg] = idx
		except Exception:
			legend_to_row_index = {}

		def _parse_rebar_from_fourth_segment(pile_type_text: str) -> tuple[Optional[int], Optional[int]]:
			if not pile_type_text:
				return None, None
			parts = pile_type_text.split("-")
			if len(parts) < 4:
				return None, None
			seg = parts[3]
			import re as _re
			m = _re.search(r"\(([^()]*)\)", seg)
			if not m:
				return None, None
			inside = m.group(1)
			m_sym = _re.search(r"^\s*(\d+)\s*(%%13[12])\s*(\d{2,3})\b", inside)
			if m_sym:
				try:
					return int(m_sym.group(1)), int(m_sym.group(3))
				except Exception:
					return None, None
			m_cnt = _re.search(r"(\d+)", inside)
			m_dia = _re.search(r"(\d{2,3})\b", inside)
			cnt_val = int(m_cnt.group(1)) if m_cnt else None
			dia_val = int(m_dia.group(1)) if m_dia else None
			return cnt_val, dia_val

		for row in reader:
			N2 = to_float(row.get(uplift_col) or "")
			N1 = to_float(row.get(force_col) or "")
			ratio = ""
			if N2 is not None and N2 != 0 and N1 is not None:
				ratio_val = abs(N1) / N2
				ratio = f"{ratio_val}"
				# classify into zones per legend
				legend = (row.get(legend_col) or "").strip() if legend_col else ""
				module_id = (row.get(module_id_col) or "").strip() if module_id_col else ""
				if ratio_val < 0.3:
					zone = "<0.3"
				elif 0.3 <= ratio_val <= 0.6:
					zone = "0.3-0.6"
				else:
					zone = ">0.6"
				# count unique ModuleNowID per (zone, legend)
				bucket = zone_counts[zone].get(legend)
				if bucket is None:
					bucket = set()
					zone_counts[zone][legend] = bucket
				if module_id:
					bucket.add(module_id)
				# keep type text if available and not recorded
				if type_col:
					ptxt = (row.get(type_col) or "").strip()
					if ptxt and legend not in zone_legend_type[zone]:
						zone_legend_type[zone][legend] = ptxt
			row_out = dict(row)
			row_out["承载力比值"] = ratio
			rows.append(row_out)

	fieldnames = list(reader.fieldnames) + (["承载力比值"] if "承载力比值" not in reader.fieldnames else [])
	output_csv.parent.mkdir(parents=True, exist_ok=True)
	with output_csv.open("w", encoding="utf-8", newline="") as f_out:
		writer = csv.DictWriter(f_out, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)

	# Also write zone summary per legend: 抗拔桩_分区计算.csv
	zone_csv = output_csv.parent / "抗拔桩_分区计算.csv"
	summary_rows = []
	# 颜色映射
	zone_color = {"<0.3": "绿色", "0.3-0.6": "黄色", ">0.6": "红色"}
	for zone_name, legend_map in zone_counts.items():
		# 先计算每个桩图例的“调整后计数”（按去重 ModuleNowID 后再减 1，底为 0）
		adjusted_counts = {}
		for legend_key, id_set in legend_map.items():
			leg_clean = (legend_key or "").strip()
			if not leg_clean:
				continue
			cnt = len(id_set)
			if cnt <= 0:
				cnt = 0
			else:
				# 仅当区域为 <0.3 时，每个桩图例计数减 1
				if zone_name == "<0.3":
					cnt = max(cnt - 1, 0)
			adjusted_counts[leg_clean] = cnt
		# 区域级：由各桩图例的调整后计数求和
		area_count = sum(adjusted_counts.values())
		# 区域总配筋数 = Σ(桩图例数量 × 该桩图例的优化前配筋数)
		area_peijin = 0
		for leg_name, leg_cnt in adjusted_counts.items():
			pile_type_text = zone_legend_type.get(zone_name, {}).get(leg_name, "")
			per_count, _per_dia = _parse_rebar_from_fourth_segment(pile_type_text)
			if per_count is not None and leg_cnt:
				area_peijin += per_count * leg_cnt
		if zone_name == "<0.3":
			area_opt = (10 - 6) * area_count
		elif zone_name == "0.3-0.6":
			area_opt = (10 - 7) * area_count
		else:
			area_opt = 0
		area_after = area_peijin - area_opt
		avg_before = (area_peijin / area_count) if area_count else 0
		avg_after = (area_after / area_count) if area_count else 0
		# 不输出区域行（仅输出带桩图例的行）
		# 先为每个桩图例计算优化结果，暂存
		leg_results: List[dict] = []
		for legend, id_set in legend_map.items():
			legend = (legend or "").strip()
			if not legend:
				continue
			count = adjusted_counts.get(legend, 0)
			pile_type_text = zone_legend_type.get(zone_name, {}).get(legend, "")
			per_count, per_dia = _parse_rebar_from_fourth_segment(pile_type_text)
			# defaults
			opt_count = ("" if per_count is None else per_count)
			opt_dia = per_dia if per_dia is not None else "待完善"
			row_idx = legend_to_row_index.get(legend)
			cand_list: List[int] = []
			y_val: Optional[float] = None
			x1_display = ""
			try:
				if row_idx is not None:
					if zone_name == "<0.3":
						cand_list = [14, 16]
						y_val = 0.3 * 800000
					elif zone_name == "0.3-0.6":
						cand_list = [18, 20]
						y_val = 0.6 * 800000
					else:
						cand_list = []
						y_val = None
					if cand_list and y_val is not None:
						vals = []
						x1_raws: List[float] = []
						for d in cand_list:
							x1 = compute_x1_flexible(y=y_val, row_index=row_idx, deq_mm=d)
							x1_raws.append(x1)
							if isinstance(x1, float) and (math.isnan(x1)):
								num = 6
							else:
								num = int(math.ceil(float(x1)))
							vals.append((d, num, num * (d ** 2)))
						vals.sort(key=lambda t: t[0])
						(d_small, n_small, s_small), (d_large, n_large, s_large) = vals[0], vals[1]
						print(f"d_small: {d_small}, n_small: {n_small}, s_small: {s_small}, d_large: {d_large}, n_large: {n_large}, s_large: {s_large}")
						if s_large - s_small <= 1.5 * s_small:
							opt_dia = d_small
							opt_count = n_small
						else:
							opt_dia = d_large
							opt_count = n_large
						x1_display = "/".join((f"{v:.2f}" if isinstance(v, float) else f"{float(v):.2f}") for v in x1_raws)
			except Exception:
				pass
			leg_results.append({
				"legend": legend,
				"count": count,
				"per_count": per_count,
				"per_dia": per_dia,
				"opt_count": opt_count,
				"opt_dia": opt_dia,
				"row_idx": row_idx,
				"cand_list": cand_list,
				"y_val": y_val,
				"x1_display": x1_display,
			})
		# 由子项汇总区域优化后配筋数与相关字段
		area_after = 0
		for lr in leg_results:
			c = lr["count"] or 0
			try:
				num = int(lr["opt_count"]) if lr["opt_count"] != "" else 0
			except Exception:
				num = 0
			area_after += (c * num)
		area_opt = area_peijin - area_after
		avg_after = (round(area_after / area_count, 1) if area_count else 0)
		# 构造区域描述
		if zone_name == "<0.3":
			ratio_phrase = "小于0.3"
		elif zone_name == "0.3-0.6":
			ratio_phrase = "介于0.3与0.6之间"
		else:
			ratio_phrase = "大于0.6"
		area_desc = (f"该区域抗拔桩桩基抗拔反力与抗拔力特征值比值{ratio_phrase}，总计（{area_count}）个桩、（{area_peijin}）根配筋。"
				     f"现可优化（{area_opt}）根配筋，优化前桩均钢筋数为（{avg_before}），优化后桩均钢筋数为（{avg_after}）")
		# 输出每个桩图例行
		for lr in leg_results:
			legend = lr["legend"]
			count = lr["count"]
			per_count = lr["per_count"]
			per_dia = lr["per_dia"]
			opt_count = lr["opt_count"]
			opt_dia = lr["opt_dia"]
			row_idx = lr["row_idx"]
			cand_list = lr["cand_list"]
			y_val = lr["y_val"]
			x1_display = lr["x1_display"]
			# 构造桩级描述
			pile_desc = (
				f"在抗拔桩桩基抗拔反力与抗拔力特征值比值{ratio_phrase}的区域中，该桩型有共计（{count}）个桩。"
				f"优化前主筋配置为（{'' if per_count is None else per_count}）根（{'' if per_dia is None else per_dia}）mm；"
				f"优化后调整为（{opt_count}）根（{opt_dia}）mm。"
			)
			summary_rows.append({
				"区域名": zone_name,
				"区域承载力比值": zone_name,
				"区域桩颜色": zone_color.get(zone_name, ""),
				"区域桩数量": area_count,
				"区域总配筋数": area_peijin,
				"区域可优化配筋数": area_opt,
				"区域优化后配筋数": area_after,
				"优化前桩均钢筋数": avg_before,
				"优化后桩均钢筋数": avg_after,
				"区域描述": area_desc,
				"桩图例": legend,
				"桩数量": count,
				"优化前钢筋数": ("" if per_count is None else per_count),
				"优化后钢筋数": opt_count,
				"优化前钢筋直径": ("" if per_dia is None else per_dia),
				"优化后钢筋直径": opt_dia,
				"row_index": ("" if row_idx is None else row_idx),
				"候选直径": ("/".join(str(d) for d in cand_list) if cand_list else ""),
				"y": ("" if y_val is None else y_val),
				"x1": x1_display,
				"描述": pile_desc,
			})
	with zone_csv.open("w", encoding="utf-8-sig", newline="") as fz:
		writer = csv.DictWriter(fz, fieldnames=[
			"区域名",
			"区域承载力比值",
			"区域桩颜色",
			"区域桩数量",
			"区域总配筋数",
			"区域可优化配筋数",
			"区域优化后配筋数",
			"优化前桩均钢筋数",
			"优化后桩均钢筋数",
			"区域描述",
			"桩图例",
			"桩数量",
			"优化前钢筋数",
			"优化后钢筋数",
			"优化前钢筋直径",
			"优化后钢筋直径",
			"row_index",
			"候选直径",
			"y",
			"x1",
			"描述",
		])
		writer.writeheader()
		writer.writerows(summary_rows)

	print("已写入到: ", zone_csv)
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