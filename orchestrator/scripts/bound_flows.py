"""Build the CONUS flow statistics table.

This is what `settings.flow_statistics` names by default (see
flow_statistics.py) -- the table author-intent.py and f2f.py fall back to when
an AOI config names none of its own. One-time data prep, not part of the
runbook or `just`: rerun it only to regenerate that table, then
`just stage-source-data <out> flows/<name>.parquet` to publish it.

Joins NHF flowpaths to an AEP source table (e.g. NWM flows v3) on the NHF
reference flowpath id, fits log-log drainage-area regressions per return-period
column to fill gaps and clip outliers to the 95% prediction interval, estimates
bankfull depth, and writes one parquet with a text reach_id column -- the NHF
flowpath id, which modify_network keeps as the downstream reach's id when it
merges reaches, so it matches a network's reach_id.

Usage:
    uv run scripts/bound_flows.py build <nhf.gpkg> <aep-source.parquet> <out.parquet> [--sample N] [--no-plots]
    uv run scripts/bound_flows.py clip <network.gpkg> <table.parquet> <out.parquet>

`clip` subsets an already-built table to one network's reaches, e.g. testdata.

Blackburn-Lynch Bankfull Depth Citation: Blackburn-Lynch, Whitney, Carmen T.
Agouridis, and Christopher D. Barton, 2017. Development of Regional Curves for
Hydrologic Landscape Regions (HLR) in the Contiguous United States. Journal of
the American Water Resources Association (JAWRA) 53(4): 903-928.
https://doi.org/10.1111/1752-1688.12540
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["geopandas>=1.0", "pandas", "numpy", "scipy>=1.13", "matplotlib>=3.10", "pyarrow>=15"]
# ///

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.stats import linregress, t

# --- NHF layer / field names ---
NHF_REFERENCE_LAYER = "reference_flowpaths"
NHF_FLOWPATHS_LAYER = "flowpaths"
NHF_FLOWPATH_ID_FIELD = "fp_id"
NHF_REF_FLOWPATH_ID_FIELD = "ref_fp_id"
NHF_DA_COL = "total_da_sqkm"
AEP_ID_FIELD = "ID"
OUT_FLOWPATH_ID = "reach_id"

# --- Flow / frequency columns ---
SRC_FIELDS = [
    AEP_ID_FIELD,
    "high_flow_threshold",
    "stream_order",
    "f2year",
    "f5year",
    "f10year",
    "f25year",
    "f50year",
    "f100year",
]
RI_COLS = [
    "high_flow_threshold",
    "f2year",
    "f5year",
    "f10year",
    "f25year",
    "f50year",
    "f100year",
]
AEP_COLS = ["f2year", "f5year", "f10year", "f25year", "f50year", "f100year"]

# --- QC schema ---
REQUIRED_FIELDS = [
    "high_flow_threshold",
    "f2year",
    "f5year",
    "f10year",
    "f25year",
    "f50year",
    "f100year",
    "regression_q_applied",
    "bkf_depth_m",
]


def load_nhf(
    nhf_path: Path, aep_source_path: Path, sample: int | None
) -> gpd.GeoDataFrame:
    """Load NHF flowpaths, join to the AEP source table, index by integer reach_id."""
    print(f"nhf             {nhf_path}")
    nhf = gpd.read_file(
        nhf_path,
        layer=NHF_FLOWPATHS_LAYER,
        columns=[NHF_FLOWPATH_ID_FIELD, NHF_DA_COL],
        ignore_geometry=True,
    )
    reference = gpd.read_file(
        nhf_path,
        layer=NHF_REFERENCE_LAYER,
        columns=[NHF_REF_FLOWPATH_ID_FIELD, NHF_FLOWPATH_ID_FIELD],
    )
    print(f"aep source      {aep_source_path}")
    aeps = pd.read_parquet(aep_source_path, columns=SRC_FIELDS)

    nhf = nhf.merge(reference, on=NHF_FLOWPATH_ID_FIELD, how="left")
    nhf = nhf.merge(
        aeps, left_on=NHF_REF_FLOWPATH_ID_FIELD, right_on=AEP_ID_FIELD, how="left"
    )

    nhf[RI_COLS] = nhf[RI_COLS].apply(pd.to_numeric, errors="coerce") / 35.3147
    nhf = nhf.drop(columns=[NHF_REF_FLOWPATH_ID_FIELD, AEP_ID_FIELD]).rename(
        columns={NHF_FLOWPATH_ID_FIELD: OUT_FLOWPATH_ID}
    )
    nhf = nhf.set_index(OUT_FLOWPATH_ID)

    # NHF flowpath ids are integers. Held as int64 here so the id written out as
    # text is its exact digits, never a float's "123.0" or rounded mantissa.
    unkeyed = nhf.index.isna()
    if unkeyed.any():
        print(f"  dropping      {unkeyed.sum()} row(s) with no flowpath id")
        nhf = nhf[~unkeyed]
    non_integer = nhf.index.to_series().apply(lambda v: float(v) != int(v))
    if non_integer.any():
        sys.exit(
            f"{nhf_path}: {int(non_integer.sum())} flowpath id(s) are not whole numbers, e.g. {nhf.index[non_integer][:5].tolist()}"
        )
    nhf.index = nhf.index.astype("int64")

    nhf = nhf.sort_values("f100year", ascending=False)
    nhf = nhf[~nhf.index.duplicated(keep="first")]
    print(f"  loaded        {len(nhf)} reach(es)")
    if sample is not None:
        print(f"  --sample      truncating to {sample}")
        nhf = nhf.head(sample)
    return nhf


def blackburn_lynch_bkf_depth(da: float) -> float:
    """Bankfull depth in meters from drainage area in sq.km."""
    return 0.27 * (da**0.21)


def _da_regression(
    log_da: np.ndarray, log_q: np.ndarray, prediction_locations: np.ndarray
):
    """Fit log-log OLS of flow on drainage area; return (mean, lower, upper) at each prediction location."""
    fit = linregress(log_da, log_q)
    yhat = fit.intercept + fit.slope * log_da
    resid = log_q - yhat

    n = len(log_da)
    s = np.sqrt(np.sum(resid**2) / (n - 2))
    se_pred = s * np.sqrt(
        1
        + 1 / n
        + (prediction_locations - log_da.mean()) ** 2
        / np.sum((log_da - log_da.mean()) ** 2)
    )
    tcrit = t.ppf(0.975, n - 2)

    mean = fit.intercept + fit.slope * prediction_locations
    lower = np.exp(mean - tcrit * se_pred)
    upper = np.exp(mean + tcrit * se_pred)
    return np.exp(mean), lower, upper


def _compute_da_regressions(nhf_in: gpd.GeoDataFrame, all_flow_cols: list[str]):
    """Log-log DA regressions for each flow column; return (da_range, {col: (mean, lower, upper)})."""
    log_da = np.log(nhf_in[NHF_DA_COL].values)
    da_range = np.linspace(log_da.min(), log_da.max(), 200)
    regressions = {}
    for col in all_flow_cols:
        q = nhf_in[col].values
        fit_mask = q >= 1e-6
        log_q = np.log(q[fit_mask])
        regressions[col] = _da_regression(log_da[fit_mask], log_q, da_range)
    return da_range, regressions


def _plot_da_regression(nhf_in, da_range, regressions, out_path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 4, figsize=(16, 8), sharex=True, sharey=True)
    colors = np.where(nhf_in["stream_order"].values < 3, "red", "k")
    for ax, col in zip(axes.flat, regressions):
        q = nhf_in[col].clip(lower=1e-6).values
        mean, lower, upper = regressions[col]
        ax.scatter(nhf_in[NHF_DA_COL].values, q, s=0.1, alpha=1, label="data", c=colors)
        ax.plot(np.exp(da_range), mean, color="blue", label="regression")
        ax.plot(np.exp(da_range), lower, color="blue", ls="dotted")
        ax.plot(np.exp(da_range), upper, color="blue", ls="dotted")
        ax.fill_between(
            np.exp(da_range), lower, upper, alpha=0.1, color="blue", label="95% PI"
        )
        ax.axhline(1, color="gray", ls="--", lw=0.8)
        ax.set_title(col)
        ax.set_xlabel("DA (sqkm)")
        ax.set_ylabel("Discharge (cms)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_facecolor("whitesmoke")
    axes.flat[0].legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  plot          {out_path}")


def enrich_nhf(
    nhf_in: gpd.GeoDataFrame, plots: bool, out_path: Path
) -> gpd.GeoDataFrame:
    """Fill gaps and clip outliers per column with DA regression bounds; add bankfull depth."""
    all_flow_cols = RI_COLS
    da_range, regressions = _compute_da_regressions(nhf_in, all_flow_cols)
    if plots:
        _plot_da_regression(
            nhf_in,
            da_range,
            regressions,
            out_path.with_name(out_path.stem + ".da_regression.png"),
        )

    log_da = np.log(nhf_in[NHF_DA_COL].values)
    nhf_in["regression_q_applied"] = False

    nan_mask = nhf_in[RI_COLS].isna().any(axis=1)
    if nan_mask.any():
        print(
            f"  filling       {nan_mask.sum()} reach(es) with NaN RI flows, from the regression mean"
        )
        for col in RI_COLS:
            col_nan = nhf_in[col].isna()
            if not col_nan.any():
                continue
            valid = ~nhf_in[col].isna() & (nhf_in[col] >= 1e-6)
            log_q_valid = np.log(nhf_in.loc[valid, col].values)
            mean, _, _ = _da_regression(log_da[valid], log_q_valid, log_da)
            nhf_in.loc[col_nan, col] = mean[col_nan.values]
        nhf_in.loc[nan_mask, "regression_q_applied"] = True

    for col in all_flow_cols:
        q = nhf_in[col].values
        fit_mask = q >= 1e-6
        log_q = np.log(q[fit_mask])
        mean, lower, upper = _da_regression(log_da[fit_mask], log_q, log_da)
        too_low = q < lower
        too_high = q > upper
        nhf_in.loc[too_low, col] = lower[too_low]
        nhf_in.loc[too_high, col] = upper[too_high]
        nhf_in.loc[too_low | too_high, "regression_q_applied"] = True
        print(
            f"  {col:<20} {too_low.sum()} low, {too_high.sum()} high clipped to the 95% PI"
        )

    non_monotonic = ~(nhf_in[RI_COLS].diff(axis=1).iloc[:, 1:] > 0).all(axis=1)
    if non_monotonic.any():
        print(
            f"  regressing    {non_monotonic.sum()} non-monotonic reach(es) to the regression mean"
        )
        for col in RI_COLS:
            q = nhf_in[col].values
            fit_mask = q >= 1e-6
            log_q = np.log(q[fit_mask])
            mean, _, _ = _da_regression(log_da[fit_mask], log_q, log_da)
            nhf_in.loc[non_monotonic, col] = mean[non_monotonic.values]
        nhf_in.loc[non_monotonic, "regression_q_applied"] = True

    if plots:
        _plot_da_regression(
            nhf_in,
            da_range,
            regressions,
            out_path.with_name(out_path.stem + ".da_regression_after_clean.png"),
        )

    nhf_in["bkf_depth_m"] = nhf_in[NHF_DA_COL].apply(blackburn_lynch_bkf_depth)
    # Rounded up rather than to nearest, so a nonzero flow never rounds down to
    # the unusable 0.0 -- the smallest a positive value can come out is 0.1.
    nhf_in[RI_COLS] = np.ceil(nhf_in[RI_COLS] * 10) / 10
    return nhf_in


def qc(df: pd.DataFrame) -> list[str]:
    """Every problem with the built table; empty means it is fit to publish."""
    errors = []
    if df.index.name != OUT_FLOWPATH_ID:
        errors.append(f"index not named {OUT_FLOWPATH_ID!r}")
    if not pd.api.types.is_integer_dtype(df.index):
        errors.append(f"index dtype is not integer: {df.index.dtype}")
    missing_fields = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing_fields:
        errors.append(f"missing fields: {missing_fields}")
    nan_fields = df[REQUIRED_FIELDS].isnull().sum()
    nan_fields = nan_fields[nan_fields > 0].to_dict()
    if nan_fields:
        errors.append(f"NaN values found: {nan_fields}")
    if df.index.duplicated().any():
        errors.append(f"duplicate reach ids: {df.index.duplicated().sum()}")
    non_monotonic = ~(df[AEP_COLS].diff(axis=1).iloc[:, 1:] >= 0).all(axis=1)
    if non_monotonic.any():
        errors.append(f"non-monotonic AEP discharges: {non_monotonic.sum()} reach(es)")
    zero_discharge = (df[AEP_COLS] == 0).any(axis=1).sum()
    if zero_discharge:
        errors.append(f"zero AEP discharge: {zero_discharge} reach(es)")
    if (df["bkf_depth_m"] == 0).any():
        errors.append(
            f"zero bankfull depth: {(df['bkf_depth_m'] == 0).sum()} reach(es)"
        )
    return errors


def write(df: pd.DataFrame, out_path: Path) -> None:
    """Write, with reach_id as the first column rather than a trailing index, as text like every reach id."""
    table = df.reset_index()
    table[OUT_FLOWPATH_ID] = table[OUT_FLOWPATH_ID].astype(str)
    table.to_parquet(out_path, index=False)


def build(
    nhf_path: Path,
    aep_source_path: Path,
    out_path: Path,
    sample: int | None,
    plots: bool,
) -> None:
    nhf = load_nhf(nhf_path, aep_source_path, sample)
    enriched = enrich_nhf(nhf, plots, out_path)

    n_regression = int(enriched["regression_q_applied"].sum())
    print(f"regression applied to {n_regression} reach(es)")

    errors = qc(enriched)
    summary = {
        "path": str(out_path),
        "n_reaches": len(enriched),
        "errors": errors,
        "regression_q_applied_count": n_regression,
    }
    out_path.with_suffix(".qc.json").write_text(json.dumps(summary, indent=2))
    if errors:
        sys.exit("QC failed:\n" + "\n".join(errors))

    write(enriched, out_path)
    print(f"wrote           {out_path} ({len(enriched)} reach(es))")


def clip(network_path: Path, table_path: Path, out_path: Path) -> None:
    """Subset an already-built table to one network's reaches (e.g. testdata)."""
    net = gpd.read_file(network_path, layer="reach_network")
    reach_ids = set(net["reach_id"].astype("int64").tolist())
    print(f"network         {network_path} ({len(reach_ids)} reach(es))")

    table = pd.read_parquet(table_path)
    if OUT_FLOWPATH_ID in table.columns:
        table = table.set_index(OUT_FLOWPATH_ID)
    print(f"table           {table_path} ({len(table)} row(s))")

    subset = table.loc[table.index.isin(reach_ids)]
    missing = reach_ids - set(subset.index)
    if missing:
        sys.exit(
            f"{len(missing)} reach(es) not found in {table_path}: {sorted(missing)}"
        )

    write(subset, out_path)
    print(f"wrote           {out_path} ({len(subset)} row(s))")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="what", required=True)

    b = sub.add_parser(
        "build", help="build the flow statistics table from NHF and an AEP source"
    )
    b.add_argument(
        "nhf", type=Path, help="NHF geopackage (layers flowpaths, reference_flowpaths)"
    )
    b.add_argument(
        "aep_source", type=Path, help="AEP source parquet (e.g. NWM flows v3)"
    )
    b.add_argument("out", type=Path, help="output parquet")
    b.add_argument(
        "--sample",
        type=int,
        default=None,
        help="truncate to N reaches, for a quick run",
    )
    b.add_argument(
        "--no-plots",
        dest="plots",
        action="store_false",
        help="skip the DA regression plots",
    )

    c = sub.add_parser(
        "clip", help="subset an already-built table to one network's reaches"
    )
    c.add_argument(
        "network", type=Path, help="network geopackage (layer reach_network)"
    )
    c.add_argument("table", type=Path, help="an already-built flow statistics parquet")
    c.add_argument("out", type=Path, help="output parquet")

    args = ap.parse_args()
    if args.what == "build":
        build(args.nhf, args.aep_source, args.out, args.sample, args.plots)
    else:
        clip(args.network, args.table, args.out)


if __name__ == "__main__":
    main()
