import argparse
import csv
import math
from pathlib import Path
from typing import List, Dict, Union


def parse_float(value: str) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return float('nan')


def parse_y_list(y_str: str) -> List[float]:
    # Accept comma/space/semicolon separated lists
    separators = [',', ';', ' ']  # will normalize to commas
    normalized = y_str
    for sep in separators:
        normalized = normalized.replace(sep, ',')
    parts = [p for p in normalized.split(',') if p.strip() != '']
    y_values: List[float] = []
    for p in parts:
        try:
            # Support scientific notation like 1e6
            y_values.append(float(p))
        except Exception:
            raise ValueError(f"Invalid y value: {p}")
    if not y_values:
        raise ValueError("No valid y values provided")
    return y_values


def compute_intermediates(row: Dict[str, str]) -> Dict[str, float]:
    # Required columns
    # w, Aps(mm^2), deq(mm), acr, ftk(N/mm^2), Es(N/mm^2), c(mm)
    w = parse_float(row.get('w', 'nan'))
    Aps = parse_float(row.get('Aps(mm^2)', 'nan'))
    deq = parse_float(row.get('deq(mm)', 'nan'))
    acr = parse_float(row.get('acr', 'nan'))
    ftk = parse_float(row.get('ftk(N/mm^2)', 'nan'))
    Es = parse_float(row.get('Es(N/mm^2)', 'nan'))
    c = parse_float(row.get('c(mm)', 'nan'))

    pi = math.pi

    # K = 0.65 * ftk * Aps / 1.1
    K = 0.65 * ftk * Aps / 1.1

    # a = w * Es / (1.1 * acr) * pi^2 / 4 * deq^3
    # Expand carefully to avoid precedence mistakes
    a = (w * Es) / (1.1 * acr) * (pi ** 2) / 4.0 * (deq ** 3)

    # b = 1.9 * pi * c * deq
    b = 1.9 * pi * c * deq

    # r = 0.32 * Aps
    r = 0.32 * Aps

    return {
        'K': K,
        'a': a,
        'b': b,
        'r': r,
        'w': w,
        'Aps(mm^2)': Aps,
        'deq(mm)': deq,
        'acr': acr,
        'ftk(N/mm^2)': ftk,
        'Es(N/mm^2)': Es,
        'c(mm)': c,
    }


def solve_x_values(K: float, a: float, b: float, r: float, y: float) -> Dict[str, float]:
    dy = y - K

    # Handle a == 0 (degenerate). In that rare case, we cannot use the quadratic form safely.
    if a == 0 or math.isnan(a):
        return {'x1': float('nan'), 'x2': float('nan')}

    discriminant = (dy ** 2) * (b ** 2) + 4.0 * a * dy * r

    # Numerical guard: clamp very small negative to zero
    if discriminant < 0 and discriminant > -1e-12:
        discriminant = 0.0

    if discriminant < 0:
        return {'x1': float('nan'), 'x2': float('nan')}

    sqrt_disc = math.sqrt(discriminant)
    denom = 2.0 * a

    x1 = ((dy * b) + sqrt_disc) / denom
    x2 = ((dy * b) - sqrt_disc) / denom
    return {'x1': x1, 'x2': x2}

# 调用这个
def compute_x1_for_row(y: float, row_index: int, input_csv: Union[str, Path] = Path('outputs') / '抗拔桩_参数计算.csv') -> float:
    """Compute x1 for a given y and CSV row (1-based index).

    Parameters
    ----------
    y : float
        The y value to use in the quadratic expression.
    row_index : int
        1-based index of the target row in the input CSV (excluding header).
    input_csv : Union[str, Path]
        Path to the input CSV. Defaults to outputs/抗拔桩_参数计算.csv.

    Returns
    -------
    float
        The computed x1 value (may be NaN if discriminant < 0 or invalid inputs).
    """
    path = Path(input_csv)
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    # Read and keep non-empty rows
    rows: List[Dict[str, str]] = []
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not any(v.strip() for v in row.values() if v is not None):
                continue
            rows.append(row)

    if row_index < 1 or row_index > len(rows):
        raise IndexError(f"row_index out of range: {row_index} (valid 1..{len(rows)})")

    row = rows[row_index - 1]
    mids = compute_intermediates(row)
    xs = solve_x_values(mids['K'], mids['a'], mids['b'], mids['r'], y)
    return xs['x1']


def compute_x1_flexible(
    y: float | None = None,
    row_index: int | None = None,
    input_csv: Union[str, Path] = Path('outputs') / '抗拔桩_参数计算.csv',
    w: Union[str, float, None] = None,
    Aps_mm2: Union[str, float, None] = None,
    deq_mm: Union[str, float, None] = None,
    acr: Union[str, float, None] = None,
    ftk_N_mm2: Union[str, float, None] = None,
    Es_N_mm2: Union[str, float, None] = None,
    c_mm: Union[str, float, None] = None,
) -> float:
    """Compute x1 using either explicit parameters or by reading a CSV row.

    Behavior:
    - If row_index and input_csv are provided, unspecified parameters are read from the
      CSV row (1-based, excluding header). Any explicitly passed parameter overrides
      the CSV value. If y is not provided, tries to read y from CSV column 'Nk(N)'.
    - If row_index is None, caller must provide all required parameters AND y.

    Parameters
    ----------
    y : float | None
        The y value to use in the quadratic expression. If None and row_index is
        provided, tries reading 'Nk(N)' from the CSV.
    row_index : int | None
        1-based row index (excluding header) to read defaults from the CSV.
    input_csv : Path | str
        CSV path used when row_index is provided. Defaults to outputs/抗拔桩_参数计算.csv.
    w, Aps_mm2, deq_mm, acr, ftk_N_mm2, Es_N_mm2, c_mm : str|float|None
        Parameters for compute_intermediates. Strings or numbers are accepted.

    Returns
    -------
    float
        The computed x1 value (may be NaN if inputs are invalid or discriminant < 0).
    """
    # Helper to stringify values preserving empty when None
    def _to_str(v: Union[str, float, None]) -> str:
        if v is None:
            return ""
        return f"{v}"

    # Prepare a base row dict possibly from CSV
    base_row: Dict[str, str] = {
        'w': '',
        'Aps(mm^2)': '',
        'deq(mm)': '',
        'acr': '',
        'ftk(N/mm^2)': '',
        'Es(N/mm^2)': '',
        'c(mm)': '',
        'Nk(N)': '',
    }

    if row_index is not None:
        path = Path(input_csv)
        if not path.exists():
            raise FileNotFoundError(f"Input CSV not found: {path}")
        rows: List[Dict[str, str]] = []
        with path.open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Skip empty lines
                if not any((v or '').strip() for v in row.values() if v is not None):
                    continue
                rows.append(row)
        if row_index < 1 or row_index > len(rows):
            raise IndexError(f"row_index out of range: {row_index} (valid 1..{len(rows)})")
        src = rows[row_index - 1]
        # Fill from CSV when present
        for key in list(base_row.keys()):
            if key in src and src[key] is not None:
                base_row[key] = str(src[key]).strip()
    else:
        # Without row_index, require all parameters and y to be provided
        required = [w, Aps_mm2, deq_mm, acr, ftk_N_mm2, Es_N_mm2, c_mm, y]
        if any(v is None for v in required):
            raise ValueError("When row_index is not provided, all parameters and y must be specified.")

    # Override with explicitly provided parameters when given
    if w is not None: base_row['w'] = _to_str(w)
    if Aps_mm2 is not None: base_row['Aps(mm^2)'] = _to_str(Aps_mm2)
    if deq_mm is not None: base_row['deq(mm)'] = _to_str(deq_mm)
    if acr is not None: base_row['acr'] = _to_str(acr)
    if ftk_N_mm2 is not None: base_row['ftk(N/mm^2)'] = _to_str(ftk_N_mm2)
    if Es_N_mm2 is not None: base_row['Es(N/mm^2)'] = _to_str(Es_N_mm2)
    if c_mm is not None: base_row['c(mm)'] = _to_str(c_mm)

    # Determine y
    if y is None:
        try:
            y = float((base_row.get('Nk(N)') or '').replace(',', '').strip())
        except Exception:
            y = float('nan')

    # Compute intermediates and x1
    mids = compute_intermediates(base_row)
    xs = solve_x_values(mids['K'], mids['a'], mids['b'], mids['r'], float(y))
    return xs['x1']


def main():
    parser = argparse.ArgumentParser(description='Compute K, a, b, r and x1/x2 for a list of y values from input CSV rows.')
    parser.add_argument('--input', '-i', type=str, default=str(Path('outputs') / '抗拔桩_参数计算.csv'), help='Input CSV path')
    parser.add_argument('--output', '-o', type=str, default=str(Path('outputs') / '抗拔桩_num_results.csv'), help='Output CSV path')
    parser.add_argument('--y', default="240000,480000", type=str, help='List of y values (comma/space/semicolon separated). Example: "1e6, 1.4e6, 578000"')

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    y_values = parse_y_list(args.y)

    # Read input CSV
    rows: List[Dict[str, str]] = []
    with input_path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip empty lines
            if not any(v.strip() for v in row.values() if v is not None):
                continue
            rows.append(row)

    # Prepare output
    output_headers = [
        'row_index',
        'y',
        'K', 'a', 'b', 'r',
        'x1', 'x2',
        # Optional: echo key inputs for traceability
        'w', 'Aps(mm^2)', 'deq(mm)', 'acr', 'ftk(N/mm^2)', 'Es(N/mm^2)', 'c(mm)'
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8-sig', newline='') as f_out:
        writer = csv.DictWriter(f_out, fieldnames=output_headers)
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            mids = compute_intermediates(row)
            for y in y_values:
                xs = solve_x_values(mids['K'], mids['a'], mids['b'], mids['r'], y)
                record = {
                    'row_index': idx,
                    'y': y,
                    'K': mids['K'],
                    'a': mids['a'],
                    'b': mids['b'],
                    'r': mids['r'],
                    'x1': xs['x1'],
                    'x2': xs['x2'],
                    'w': mids['w'],
                    'Aps(mm^2)': mids['Aps(mm^2)'],
                    'deq(mm)': mids['deq(mm)'],
                    'acr': mids['acr'],
                    'ftk(N/mm^2)': mids['ftk(N/mm^2)'],
                    'Es(N/mm^2)': mids['Es(N/mm^2)'],
                    'c(mm)': mids['c(mm)'],
                }
                writer.writerow(record)

    print(f"Wrote results to: {output_path}")


if __name__ == '__main__':
    main() 