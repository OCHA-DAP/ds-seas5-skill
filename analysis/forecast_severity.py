"""Global SEAS5 forecast-extremeness heatmap: avg max(drought, flood) return period [yr],
issue year × issued month, averaged over valid trimesters & skilled (r>=0.3) countries, detrended.

Reads the processed paired/skill parquets (DEV blob) — so re-run after each monthly
compute_skill.py to refresh. Writes a dated PNG to the gitignored ``outputs/`` folder.

Run:  uv run python analysis/forecast_severity.py
"""
import calendar
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import ocha_stratus as stratus  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402

SKILL_MIN = 0.3
OUT_DIR = REPO / "outputs"


def tri_valid(months, im):
    signed = [o if (o := (m - im) % 12) <= 6 else o - 12 for m in months]
    future = [s for s in signed if s > 0]
    return all(s >= 0 for s in signed) and bool(future) and max(future) <= 6


def issue_year(sy, im, tri):
    months = TRIMESTERS[tri]
    wrap = 12 in months and 1 in months
    cross = (not wrap) and min(months) < im
    return int(sy) - (1 if cross else 0)


def main():
    paired = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/paired_yearly_detrended.parquet", stage="dev")
    skill = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats_detrended.parquet", stage="dev")
    r_of = skill.set_index(["pcode", "issued_month", "trimester"])["pearson_r"].to_dict()

    rows = []
    for (pcode, im, tri), g in paired.groupby(["pcode", "issued_month", "trimester"], sort=False):
        if not tri_valid(TRIMESTERS[tri], im):
            continue
        r = r_of.get((pcode, im, tri))
        if r is None or not (r >= SKILL_MIN):  # skilled countries only
            continue
        g = g.dropna(subset=["forecast_mean"])
        fc = g["forecast_mean"].values.astype(float)
        hist = g.loc[g["obs_mean"].notna(), "forecast_mean"].values.astype(float)
        n = len(hist)
        if n == 0:
            continue
        rp_low = (n + 1) / ((hist[None, :] < fc[:, None]).sum(axis=1) + 1)   # dry-side RP
        rp_high = (n + 1) / ((hist[None, :] > fc[:, None]).sum(axis=1) + 1)  # wet-side RP
        mx = np.maximum(rp_low, rp_high)
        for sy, m in zip(g["season_year"].values, mx):
            rows.append((im, issue_year(sy, im, tri), float(m)))

    df = pd.DataFrame(rows, columns=["issued_month", "issue_year", "max_rp"])
    piv = df.groupby(["issued_month", "issue_year"])["max_rp"].mean().unstack("issue_year")
    years = list(range(int(piv.columns.min()), int(piv.columns.max()) + 1))
    piv = piv.reindex(index=range(1, 13), columns=years)
    data = piv.values

    fig, ax = plt.subplots(figsize=(22, 6.2), dpi=150)
    norm = LogNorm(vmin=max(np.nanmin(data), 1.0), vmax=np.nanmax(data))
    im_ = ax.imshow(data, aspect="auto", cmap="YlOrRd", norm=norm)
    for i in range(12):
        for j in range(len(years)):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=6,
                        color="white" if norm(v) > 0.55 else "black")
    complete = [k for k, y in enumerate(years) if piv[y].notna().all()]
    if complete:
        ax.axvline(complete[-1] + 0.5, color="#1fb7b7", ls="--", lw=1.6)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, rotation=90, fontsize=7)
    ax.set_yticks(range(12))
    ax.set_yticklabels([calendar.month_abbr[m] for m in range(1, 13)])
    ax.set_xlabel("Issue year")
    ax.set_ylabel("Issued month")
    ax.set_title("Global SEAS5 forecast extremeness — avg max(drought, flood) RP [yr]\n"
                 "issue year × issued month (avg over valid trimesters & skilled r≥0.3 countries, detrended)")
    fig.colorbar(im_, ax=ax, pad=0.01).set_label("Avg max RP [yr] (log)")
    plt.tight_layout()

    # Name the file after the latest issuance present in the data.
    filled = [(y, m) for y in years for m in range(1, 13)
              if np.isfinite(piv.loc[m, y])]
    ly, lm = max(filled)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"forecast_severity_{ly}-{lm:02d}.png"
    plt.savefig(out, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
