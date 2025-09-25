import os
import sys
from pathlib import Path


def run_pipeline() -> int:
    base_dir = Path(__file__).parent.resolve()
    os.chdir(base_dir)

    # 1) DWG -> DXF （仅转换人防平面）
    try:
        from dwg_to_dxf import main as dwg_main
        dwg_path = base_dir / "建筑平面图_人防平面.dwg"
        if dwg_path.exists():
            print("[STEP] DWG -> DXF (人防平面)")
            code = dwg_main([str(dwg_path)])
            if code != 0:
                print("[WARN] DWG->DXF 转换返回非零退出码，后续步骤可能失败。")
        else:
            print(f"[SKIP] 未找到 DWG 文件: {dwg_path}")
    except Exception as e:
        print(f"[ERROR] DWG->DXF 失败: {e}")
        return 1

    # 2) DXF -> CSV
    try:
        from dxf2csv import main as dxf2csv_main
        print("[STEP] DXF -> CSV")
        dxf2csv_main(base_dir / "outputs")
    except Exception as e:
        print(f"[ERROR] DXF->CSV 失败: {e}")
        return 2

    # 3) 车位提取（生成 final_park.json 等）
    try:
        from extract_parking import main as extract_parking_main
        print("[STEP] 提取车位数据")
        extract_parking_main(base_dir / "outputs")
    except Exception as e:
        print(f"[ERROR] 提取车位失败: {e}")
        return 3

    # 4) 防火分区文本提取（生成 fire_zones.json）
    try:
        from extract_fire_zone_texts import print_fire_zone_texts
        print("[STEP] 提取防火分区文本并生成 JSON")
        csv_path = base_dir / "outputs" / "建筑平面图_人防平面.csv"
        print_fire_zone_texts(csv_path, "防火分区面积")
    except Exception as e:
        print(f"[ERROR] 防火分区文本提取失败: {e}")
        return 4

    # 5) 平时文字文本提取（生成 text_layer_texts.json）
    try:
        from extract_text_layer_texts import main as extract_texts_main
        print("[STEP] 提取‘平时文字’图层文本并生成 JSON")
        extract_texts_main()
    except Exception as e:
        print(f"[ERROR] 平时文字文本提取失败: {e}")
        return 5

    # 6) 可视化车位（生成 parking_debug_visualized.dxf）
    try:
        from visualize_parking_debug import main as visualize_parking_debug_main
        print("[STEP] 可视化车位")
        visualize_parking_debug_main(base_dir / "outputs")
    except Exception as e:
        print(f"[ERROR] 可视化车位失败: {e}")
        return 6


    print("[DONE] 全部步骤完成")
    return 0


if __name__ == "__main__":
    sys.exit(run_pipeline()) 