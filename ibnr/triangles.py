"""
Core chain-ladder utilities: build loss triangles, age-to-age factors,
development factors, and ultimate projections from a long-format claims file.

This replaces three near-identical copy-pasted blocks in the original script
(one each for reported claims, reported counts, reported severities) with a
single set of generic functions that work on any measure column.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def load_claims_data(filepath: str) -> pd.DataFrame:
    """Load the raw long-format claims extract.

    Expected columns: AY (accident year), Reported_year (calendar/dev year
    of the report), Reported_Claims, Reported_Counts.
    """
    df = pd.read_excel(filepath)
    required = {"AY", "Reported_year", "Reported_Claims", "Reported_Counts"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input file is missing required columns: {missing}")
    return df


def build_triangle(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Pivot long-format data into a standard loss triangle.

    Rows = accident year (AY), columns = development age in months
    (12, 24, 36, ...), values = cumulative amount for `value_col`.

    This is the generic version of `reshape_claim_data` / the inline
    duplicated blocks in the original script. Uses a proper pivot instead
    of manual list padding, so column alignment can't silently drift.
    """
    ay_values = sorted(df["AY"].unique())
    dev_periods = sorted(df["Reported_year"].unique())
    dev_ages = {period: (i + 1) * 12 for i, period in enumerate(dev_periods)}

    pivot = df.pivot_table(
        index="AY", columns="Reported_year", values=value_col, aggfunc="first"
    )
    pivot = pivot.reindex(index=ay_values, columns=dev_periods)
    pivot.columns = [dev_ages[c] for c in pivot.columns]
    return pivot


def build_severity_triangle(
    claims_triangle: pd.DataFrame, counts_triangle: pd.DataFrame, scale: float = 1000.0
) -> pd.DataFrame:
    """Severity = claims (scaled) / counts, cell by cell."""
    return (claims_triangle * scale) / counts_triangle


def age_to_age_factors(triangle: pd.DataFrame) -> pd.DataFrame:
    """Compute age-to-age (link) development factors between adjacent columns.

    Column labels are formatted like '12-24', '24-36', etc.
    The most recent accident year (which only has one diagonal cell) is
    dropped, since it can't produce a ratio.
    """
    cols = triangle.columns.tolist()
    labels = [f"{cols[i]}-{cols[i + 1]}" for i in range(len(cols) - 1)]

    factors = pd.DataFrame(index=triangle.index, columns=labels, dtype=float)
    for i, label in enumerate(labels):
        factors[label] = triangle.iloc[:, i + 1] / triangle.iloc[:, i]

    # drop the latest accident year: only one diagonal observation, no ratio possible
    latest_ay = triangle.index.max()
    factors = factors.drop(index=latest_ay, errors="ignore")
    return factors.round(3)


def development_factors(
    age_to_age: pd.DataFrame, method: str = "simple_average", n_periods: int | None = None
) -> pd.Series:
    """Selected development (link ratio) factor per development period.

    method:
        "simple_average" - straight average of all available factors in the column
        "volume_weighted" - not applicable here (needs raw triangle, see below)
        "last_n_average"  - average of the most recent `n_periods` factors only
    """
    if method == "simple_average":
        result = age_to_age.mean(axis=0)
    elif method == "last_n_average":
        if n_periods is None:
            raise ValueError("n_periods must be set for 'last_n_average'")
        result = age_to_age.apply(lambda col: col.dropna().iloc[-n_periods:].mean())
    else:
        raise ValueError(f"Unknown method: {method}")
    return result.round(3)


def volume_weighted_development_factors(triangle: pd.DataFrame) -> pd.Series:
    """Volume-weighted (all-year) link ratios: sum(col_{i+1}) / sum(col_i),
    using only accident years with data in both columns. This is the more
    standard actuarial default vs. a simple average of individual factors.
    """
    cols = triangle.columns.tolist()
    labels = [f"{cols[i]}-{cols[i + 1]}" for i in range(len(cols) - 1)]
    factors = {}
    for i, label in enumerate(labels):
        numer = triangle.iloc[:, i + 1]
        denom = triangle.iloc[:, i]
        mask = numer.notna() & denom.notna()
        factors[label] = numer[mask].sum() / denom[mask].sum()
    return pd.Series(factors).round(3)


def cumulative_development_factors(link_ratios: pd.Series) -> list[float]:
    """Convert age-to-age link ratios into cumulative development factors
    (CDFs), applied from the tail back to age 12.

    Returns a list aligned with development ages, oldest-to-latest CDF,
    ending in 1.0 for the fully-developed (latest) age.
    """
    ratios = link_ratios.tolist()[::-1]  # reverse: latest period first
    cdfs = [1.0]
    running = 1.0
    for r in ratios:
        running *= r
        cdfs.append(round(running, 3))
    return cdfs[::-1]  # re-reverse to align oldest AY -> latest AY order


def combine_frequency_severity(
    ult_counts: pd.DataFrame, ult_severity: pd.DataFrame, scale: float = 1000.0
) -> pd.DataFrame:
    """Recombine independently-projected ultimate frequency and ultimate
    severity into ultimate claims: Ultimate Claims = Ultimate Counts x
    Ultimate Severity (rescaled back to the same units as the claims
    triangle, undoing the `scale` factor used when severity was built).

    This is the actual frequency-severity technique: chain-ladder is run
    separately on the counts triangle and the severity triangle, and the
    two ultimates are multiplied together -- as opposed to running
    chain-ladder directly on the aggregate claims triangle.
    """
    combined = pd.DataFrame(index=ult_counts.index)
    combined["Ultimate_Counts"] = ult_counts["Ultimate_Counts"]
    combined["Ultimate_Severity"] = ult_severity["Ultimate_Severity"]
    combined["Ultimate_Claims_FreqSev"] = (
        combined["Ultimate_Counts"] * combined["Ultimate_Severity"] / scale
    )
    return combined.round(2)


def project_ultimates(
    triangle: pd.DataFrame, link_ratios: pd.Series, label: str = "Ultimate"
) -> pd.DataFrame:
    """Apply cumulative development factors to the latest diagonal to get
    ultimate values per accident year.
    """
    ay_order = triangle.index.tolist()
    n = len(ay_order)

    # cumulative factor from each AY's current age out to full development
    ratios_reversed = link_ratios.tolist()[::-1]
    cdf_by_age_from_end = [1.0]
    running = 1.0
    for r in ratios_reversed:
        running *= r
        cdf_by_age_from_end.append(round(running, 3))
    # cdf_by_age_from_end[0] applies to the most mature (oldest) AY, [-1] to the
    # least mature (most recent) AY. ay_order is sorted oldest -> newest, so the
    # mapping is direct (no reversal) -- this was a real bug caught during testing.
    cdfs_for_each_ay = cdf_by_age_from_end[:n]

    latest_diagonal = []
    for ay in ay_order:
        row = triangle.loc[ay].dropna()
        latest_diagonal.append(row.iloc[-1] if len(row) else np.nan)

    result = pd.DataFrame(index=ay_order)
    result["Latest_Reported"] = latest_diagonal
    result["CDF"] = cdfs_for_each_ay
    result[label] = result["Latest_Reported"] * result["CDF"]
    result["IBNR"] = result[label] - result["Latest_Reported"]
    return result.round(2)
