"""
End-to-end IBNR chain-ladder pipeline.

Usage:
    python -m ibnr.pipeline --file path/to/claims.xlsx

Produces reported-claims, reported-counts, and reported-severity triangles,
their age-to-age factors, selected development factors, and ultimate /
IBNR projections. Results are returned as a dict of DataFrames so they can
be reused directly by the Dash dashboard (see dashboard.py).
"""

from __future__ import annotations

import argparse

import pandas as pd

from .triangles import (
    age_to_age_factors,
    build_severity_triangle,
    build_triangle,
    combine_frequency_severity,
    development_factors,
    load_claims_data,
    project_ultimates,
    volume_weighted_development_factors,
)


SEVERITY_SCALE = 1000.0


def run_pipeline(filepath: str, dev_method: str = "simple_average") -> dict:
    df = load_claims_data(filepath)

    # Triangles
    claims_tri = build_triangle(df, "Reported_Claims")
    counts_tri = build_triangle(df, "Reported_Counts")
    severity_tri = build_severity_triangle(claims_tri, counts_tri, scale=SEVERITY_SCALE)

    # Age-to-age factors
    claims_ata = age_to_age_factors(claims_tri)
    counts_ata = age_to_age_factors(counts_tri)
    severity_ata = age_to_age_factors(severity_tri)

    # Selected development factors (simple average by default; volume-weighted
    # available as a more standard alternative)
    if dev_method is None:
        # Don't calculate development factors - just return age-to-age factors
        claims_devfac = None
        counts_devfac = None
        severity_devfac = None
    elif dev_method == "volume_weighted":
        claims_devfac = volume_weighted_development_factors(claims_tri)
        counts_devfac = volume_weighted_development_factors(counts_tri)
        severity_devfac = volume_weighted_development_factors(severity_tri)
    else:
        claims_devfac = development_factors(claims_ata, method=dev_method)
        counts_devfac = development_factors(counts_ata, method=dev_method)
        severity_devfac = development_factors(severity_ata, method=dev_method)

    # --- Method 1: chain-ladder applied directly to the aggregate claims triangle ---
    # --- Method 2: frequency-severity technique ---
    # Only calculate ultimates if development factors are provided
    if dev_method is not None:
        ult_claims_chainladder = project_ultimates(claims_tri, claims_devfac, label="Ultimate_Claims")

        # Chain-ladder run separately on counts and severity triangles, then
        # ultimate claims = ultimate counts x ultimate severity (rescaled).
        ult_counts = project_ultimates(counts_tri, counts_devfac, label="Ultimate_Counts")
        ult_severity = project_ultimates(severity_tri, severity_devfac, label="Ultimate_Severity")
        ult_claims_freqsev = combine_frequency_severity(ult_counts, ult_severity, scale=SEVERITY_SCALE)

        # Side-by-side comparison of the two ultimate claims estimates
        comparison = pd.DataFrame(index=claims_tri.index)
        comparison["Latest_Reported_Claims"] = ult_claims_chainladder["Latest_Reported"]
        comparison["Ultimate_ChainLadder"] = ult_claims_chainladder["Ultimate_Claims"]
        comparison["Ultimate_FreqSeverity"] = ult_claims_freqsev["Ultimate_Claims_FreqSev"]
        comparison["Difference"] = (
            comparison["Ultimate_ChainLadder"] - comparison["Ultimate_FreqSeverity"]
        ).round(2)
    else:
        ult_claims_chainladder = None
        ult_counts = None
        ult_severity = None
        ult_claims_freqsev = None
        comparison = None

    return {
        "raw_data": df,
        "triangles": {
            "claims": claims_tri,
            "counts": counts_tri,
            "severity": severity_tri,
        },
        "age_to_age": {
            "claims": claims_ata,
            "counts": counts_ata,
            "severity": severity_ata,
        },
        "development_factors": {
            "claims": claims_devfac,
            "counts": counts_devfac,
            "severity": severity_devfac,
        },
        "ultimates": {
            "chain_ladder_claims": ult_claims_chainladder,
            "counts": ult_counts,
            "severity": ult_severity,
            "freq_severity_claims": ult_claims_freqsev,
            "comparison": comparison,
        },
    }


def _main():
    parser = argparse.ArgumentParser(description="Run the IBNR chain-ladder pipeline.")
    parser.add_argument("--file", required=True, help="Path to the claims Excel file")
    parser.add_argument(
        "--method",
        default="simple_average",
        choices=["simple_average", "volume_weighted", "last_n_average"],
        help="Development factor selection method",
    )
    args = parser.parse_args()

    results = run_pipeline(args.file, dev_method=args.method)

    print("\n=== Reported Claims Triangle ===")
    print(results["triangles"]["claims"])
    print("\n=== Selected Development Factors (Claims) ===")
    print(results["development_factors"]["claims"])
    print("\n=== Ultimate Claims: Chain-Ladder vs. Frequency-Severity ===")
    print(results["ultimates"]["comparison"])


if __name__ == "__main__":
    _main()
