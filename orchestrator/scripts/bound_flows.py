# TODO: Reasonable WSE bound estimates

import json
import logging
import sys
from pathlib import Path
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import t
from scipy.stats import linregress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Input / output paths ---
NHF_PATH = Path(__file__).parent.parent / "source_data" / "nhf_1.2.3.gpkg"
AEP_SRC_PATH = Path(__file__).parent.parent / "source_data" / "nwm_flows_v3_bbox_column.parquet"
OUT_DATASET_PATH = Path(__file__).parent / "min_max_network_flows.parquet"

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
EXTERNAL = Path(__file__).resolve().parents[2] / "external"
DEFAULT_NETWORK_GPKG = TESTDATA / "network.gpkg"
DEFAULT_SOURCE_PARQUET = EXTERNAL / "min_max_network_flows.parquet"
DEFAULT_TESTDATA_OUTPUT = TESTDATA / "min_max_network_flows.parquet"

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
RI_COLS = ["high_flow_threshold", "f2year", "f5year", "f10year", "f25year", "f50year", "f100year"]
AEP_COLS = ["f2year", "f5year", "f10year", "f25year", "f50year", "f100year"]
P_RI_COLS = 1 - 1 / np.array([2, 5, 10, 25, 50, 100])
LP3_FIT_RMSE_THRESHOLD = 0.10  # flag reaches where LP3 RMSE exceeds 10% of mean flow
FREQUENCY_FACTOR_RANGE = (-3.0, 3.0)

# --- QC schema ---
INDEX_NAME = "reach_id"
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

# --- Run options ---
INCLUDE_GEOMETRY = False
DEV_SAMPLE: int | None = None  # set to None for full run

# Blackburn-Lynch Bankfull Depth Citation
# Blackburn-Lynch, Whitney, Carmen T. Agouridis, and Christopher D. Barton, 2017. Development of Regional
# Curves for Hydrologic Landscape Regions (HLR) in the Contiguous United States. Journal of the American
# Water Resources Association (JAWRA) 53(4): 903-928. https://doi.org/10.1111/1752-1688.12540


def load_nhf() -> gpd.GeoDataFrame:
    """Load NHF and join to AEP_SRC."""
    log.info("Loading NHF flowpaths")
    nhf = gpd.read_file(
        NHF_PATH,
        layer=NHF_FLOWPATHS_LAYER,
        columns=[NHF_FLOWPATH_ID_FIELD, NHF_DA_COL],
        ignore_geometry=not INCLUDE_GEOMETRY,
    )
    reference = gpd.read_file(
        NHF_PATH,
        layer=NHF_REFERENCE_LAYER,
        columns=[NHF_REF_FLOWPATH_ID_FIELD, NHF_FLOWPATH_ID_FIELD],
    )
    aeps = pd.read_parquet(AEP_SRC_PATH, columns=SRC_FIELDS)

    nhf = nhf.merge(
        reference,
        left_on=NHF_FLOWPATH_ID_FIELD,
        right_on=NHF_FLOWPATH_ID_FIELD,
        how="left",
    )
    nhf = nhf.merge(
        aeps, left_on=NHF_REF_FLOWPATH_ID_FIELD, right_on=AEP_ID_FIELD, how="left"
    )

    nhf[RI_COLS] = nhf[RI_COLS].apply(pd.to_numeric, errors="coerce") / 35.3147
    nhf = nhf.drop(columns=[NHF_REF_FLOWPATH_ID_FIELD, AEP_ID_FIELD]).rename(
        columns={NHF_FLOWPATH_ID_FIELD: OUT_FLOWPATH_ID}
    )
    nhf = nhf.set_index(OUT_FLOWPATH_ID)
    nhf.index = nhf.index.astype(int)

    nhf = nhf.sort_values("f100year", ascending=False)
    nhf = nhf[~nhf.index.duplicated(keep="first")]
    log.info("Loaded %d reaches", len(nhf))
    if DEV_SAMPLE is not None:
        log.warning("DEV_SAMPLE=%d — truncating for debugging", DEV_SAMPLE)
        nhf = nhf.head(DEV_SAMPLE)
    return nhf


def blackburn_lynch_bkf_depth(da: float) -> float:
    """Calculate bankful depth in meters from drainage area in sq.km."""
    return 0.27 * (da**0.21)


def _da_regression(
    log_da: np.ndarray, log_q: np.ndarray, prediction_locations: np.ndarray
):
    """Fit log-log OLS of flow on drainage area; return (coeffs, X, se, t_crit)."""
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


def _compute_da_regressions(
    nhf_in: gpd.GeoDataFrame, all_flow_cols: list[str]
) -> tuple[np.ndarray, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    """Compute log-log DA regressions for each flow column; return (da_range, {col: (mean, lower, upper)})."""
    log_da = np.log(nhf_in[NHF_DA_COL].values)
    da_range = np.linspace(log_da.min(), log_da.max(), 200)
    regressions = {}
    for col in all_flow_cols:
        q = nhf_in[col].values
        fit_mask = q >= 1e-6
        log_q = np.log(q[fit_mask])
        regressions[col] = _da_regression(log_da[fit_mask], log_q, da_range)
    return da_range, regressions


def _plot_da_regression(
    nhf_in: gpd.GeoDataFrame,
    da_range: np.ndarray,
    regressions: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    fname: str = "da_regression.png",
) -> None:
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
    fig.savefig(Path(__file__).parent / fname, dpi=150)
    plt.close(fig)


def enrich_nhf(nhf_in: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    log.info("Building drainage area regressions and supplemental plot")
    all_flow_cols = RI_COLS
    da_range, regressions = _compute_da_regressions(nhf_in, all_flow_cols)
    _plot_da_regression(nhf_in, da_range, regressions)

    log.info("Applying regression bounds across %d AEP columns", len(all_flow_cols))
    log_da = np.log(nhf_in[NHF_DA_COL].values)
    nhf_in["regression_q_applied"] = False

    # Fill NaN RI flows with regression mean
    nan_mask = nhf_in[RI_COLS].isna().any(axis=1)
    if nan_mask.any():
        log.info(
            "Filling NaN RI flows for %d reaches with regression mean", nan_mask.sum()
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
        log.info(
            "  %s: %d low, %d high clipped to PI bounds",
            col,
            too_low.sum(),
            too_high.sum(),
        )

    # Apply regression mean to non-monotonic reaches
    non_monotonic = ~(nhf_in[RI_COLS].diff(axis=1).iloc[:, 1:] > 0).all(axis=1)
    if non_monotonic.any():
        log.info(
            "Applying regression mean to %d non-monotonic reaches", non_monotonic.sum()
        )
        for col in RI_COLS:
            q = nhf_in[col].values
            fit_mask = q >= 1e-6
            log_q = np.log(q[fit_mask])
            mean, _, _ = _da_regression(log_da[fit_mask], log_q, log_da)
            nhf_in.loc[non_monotonic, col] = mean[non_monotonic.values]
        nhf_in.loc[non_monotonic, "regression_q_applied"] = True

    _plot_da_regression(nhf_in, da_range, regressions, "da_regression_after_clean.png")

    log.info("Estimating bankfull depth")
    nhf_in["bkf_depth_m"] = nhf_in[NHF_DA_COL].apply(blackburn_lynch_bkf_depth)

    return nhf_in


def export_final(gdf: gpd.GeoDataFrame) -> None:
    log.info("Exporting %d reaches to %s", len(gdf), OUT_DATASET_PATH)
    if not INCLUDE_GEOMETRY and "geometry" in gdf.columns:
        gdf = gdf.drop(columns="geometry")
    gdf.to_parquet(OUT_DATASET_PATH)
    log.info("Export complete")


def make_bounded_flow_dataset() -> None:
    """Generate min/max flows dataset."""
    joined_nhf = load_nhf()
    enriched_nhf = enrich_nhf(joined_nhf)
    export_final(enriched_nhf)


def qc_dataset() -> None:
    """Validate that the min/max flows dataset has all valid data/passes QC checks."""
    errors = []

    if not OUT_DATASET_PATH.exists():
        raise FileNotFoundError(OUT_DATASET_PATH)

    df = pd.read_parquet(OUT_DATASET_PATH)

    if df.index.name != INDEX_NAME:
        errors.append(f"Index name not set to {INDEX_NAME}")

    if not pd.api.types.is_integer_dtype(df.index):
        errors.append(f"Index dtype is not integer: {df.index.dtype}")

    missing_fields = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing_fields:
        errors.append(f"Missing fields: {missing_fields}")

    nan_counts = df[REQUIRED_FIELDS].isnull().sum()
    nan_fields = nan_counts[nan_counts > 0].to_dict()
    if nan_fields:
        errors.append(f"NaN values found: {nan_fields}")

    if df.index.duplicated().any():
        n = df.index.duplicated().sum()
        errors.append(f"Duplicate reach IDs: {n}")

    non_monotonic = ~(df[AEP_COLS].diff(axis=1).iloc[:, 1:] >= 0).all(axis=1)
    if non_monotonic.any():
        errors.append(f"Non-monotonic AEP discharges: {non_monotonic.sum()} reaches")

    zero_discharge = (df[AEP_COLS] == 0).any(axis=1).sum()
    if zero_discharge:
        errors.append(f"Zero AEP discharges: {zero_discharge} reaches")

    if (df["bkf_depth_m"] == 0).any():
        errors.append(f"Zero bankfull_depth: {(df['bkf_depth_m'] == 0).sum()} reaches")

    n_regression = int(df["regression_q_applied"].sum())
    print(f"regression_q_applied: {n_regression} reaches")

    summary = {
        "path": str(OUT_DATASET_PATH),
        "n_reaches": len(df),
        "errors": errors,
        "regression_q_applied_count": n_regression,
    }

    qc_summary_path = OUT_DATASET_PATH.with_suffix(".qc.json")
    qc_summary_path.write_text(json.dumps(summary, indent=2))

    if errors:
        raise ValueError(
            f"QC failed with {len(errors)} error(s):\n" + "\n".join(errors)
        )

    print(f"QC passed. Summary written to {qc_summary_path}")


def clip_for_testdata() -> None:
    """Subest the min/max dataset for the reaches in testdata."""
    for p in (DEFAULT_NETWORK_GPKG, DEFAULT_SOURCE_PARQUET):
        if not p.exists():
            sys.exit(f"No such file: {p}")

    net = gpd.read_file(DEFAULT_NETWORK_GPKG, layer="reach_network")
    reach_ids = set(net["reach_id"].astype("int64").tolist())
    print(f"network  {DEFAULT_NETWORK_GPKG} ({len(reach_ids)} reaches)")

    bounds = pd.read_parquet(DEFAULT_SOURCE_PARQUET)
    print(f"source   {DEFAULT_SOURCE_PARQUET} ({len(bounds)} rows)")

    subset = bounds.loc[bounds.index.isin(reach_ids)]
    missing = reach_ids - set(subset.index)
    if missing:
        sys.exit(f"{len(missing)} reaches not found in source parquet: {sorted(missing)}")

    subset.to_parquet(DEFAULT_TESTDATA_OUTPUT)
    print(f"wrote    {DEFAULT_TESTDATA_OUTPUT} ({len(subset)} rows)")


if __name__ == "__main__":
    make_bounded_flow_dataset()
    qc_dataset()
    clip_for_testdata()
