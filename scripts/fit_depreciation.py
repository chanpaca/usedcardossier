"""Refit the used-value retention curve. Reproduces data/depreciation_params.json.

This is an OFFLINE script. The app never runs it and does not depend on pandas
or pyarrow -- only the fitted coefficients are committed. It exists so the
number in the report can be checked rather than taken on trust.

    pip install pandas pyarrow
    python -m scripts.fit_depreciation path/to/listings.csv

Input: any CSV with columns price, msrp, miles, year, body (and optionally a
powertrain column). The published fit used the Marketcheck
`us_used_car_inventory_data` sample on HuggingFace, non-BEV rows only.

Model
-----
    log(price / msrp) = b0 + b_age*age + b_miles*(miles/10k) + b_body

Body effects are dummies with Sedan as the reference level. The band
multipliers are the 15th and 85th percentiles of the residual, exponentiated,
which is what makes the UI show a range instead of a false point estimate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

AS_OF_YEAR = 2026
BODY_LEVELS = (
    "Pickup", "SUV", "Sedan", "Coupe", "Hatchback", "Convertible", "Van", "Wagon"
)
REFERENCE_BODY = "Sedan"
MIN_ROWS_PER_BODY = 15


def main(csv_path: str) -> int:
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        print("needs pandas and numpy:  pip install pandas pyarrow", file=sys.stderr)
        return 1

    raw = pd.read_csv(csv_path, low_memory=False)

    # Accept either plain names or the Marketcheck `neo_` prefixed ones.
    def col(*names: str) -> pd.Series:
        for name in names:
            if name in raw.columns:
                return raw[name]
        raise KeyError(f"none of {names} found in {csv_path}")

    df = pd.DataFrame(
        {
            "price": pd.to_numeric(col("price"), errors="coerce"),
            "msrp": pd.to_numeric(col("msrp", "neo_base_msrp"), errors="coerce"),
            "miles": pd.to_numeric(col("miles", "mileage"), errors="coerce"),
            "year": pd.to_numeric(col("year", "neo_year"), errors="coerce"),
            "body": col("body", "neo_body_type").astype(str),
        }
    ).dropna()

    powertrain = None
    for name in ("powertrain", "neo_powertrain_type"):
        if name in raw.columns:
            powertrain = raw[name].astype(str)
            break

    df["age"] = AS_OF_YEAR - df["year"]
    df = df[
        df.age.between(1, 15)
        & df.price.between(3_000, 120_000)
        & (df.msrp > 5_000)
        & df.miles.between(1_000, 250_000)
    ]

    if powertrain is not None:
        # Battery EVs follow a different depreciation path and are out of scope.
        keep = ~powertrain.reindex(df.index).str.contains(
            "Electric|BEV", case=False, na=False
        )
        df = df[keep]

    df = df[df.body.isin(BODY_LEVELS)].copy()
    df["ret"] = df.price / df.msrp
    df["log_ret"] = np.log(df.ret)
    df["miles_per_10k"] = df.miles / 10_000

    levels = [
        b for b in BODY_LEVELS
        if b != REFERENCE_BODY and (df.body == b).sum() >= MIN_ROWS_PER_BODY
    ]
    design = np.column_stack(
        [np.ones(len(df)), df.age.values, df.miles_per_10k.values]
        + [(df.body == b).astype(float).values for b in levels]
    )
    names = ["intercept", "age", "miles_per_10k"] + [f"body_{b}" for b in levels]

    beta, *_ = np.linalg.lstsq(design, df.log_ret.values, rcond=None)
    predicted = design @ beta
    r2 = 1 - (
        ((df.log_ret - predicted) ** 2).sum()
        / ((df.log_ret - df.log_ret.mean()) ** 2).sum()
    )
    pct_error = np.abs(np.exp(predicted) * df.msrp - df.price) / df.price
    lo, hi = np.quantile(df.log_ret - predicted, [0.15, 0.85])

    params = {
        "fitted_on": Path(csv_path).name,
        "n": int(len(df)),
        "as_of_year": AS_OF_YEAR,
        "r2": round(float(r2), 4),
        "median_abs_pct_error": round(float(np.median(pct_error)), 4),
        "coef": {n: round(float(b), 5) for n, b in zip(names, beta)},
        "band_low_mult": round(float(np.exp(lo)), 4),
        "band_high_mult": round(float(np.exp(hi)), 4),
    }

    out = Path(__file__).resolve().parent.parent / "data" / "depreciation_params.json"
    out.write_text(json.dumps(params, indent=2), encoding="utf-8")

    print(f"n = {params['n']}   R2 = {params['r2']}")
    print(f"median abs pct error = {params['median_abs_pct_error']:.1%}")
    print(f"band = {params['band_low_mult']}x .. {params['band_high_mult']}x")
    for name, value in params["coef"].items():
        print(f"  {name:20s} {value:+.5f}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
