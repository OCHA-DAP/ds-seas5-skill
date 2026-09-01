"""Summary map for the Uganda OND 2026 flood-trigger plan page.

One map: FloodScan OND flood recurrence (share of seasons 1998-2025 with
SFED >= 0.05, from compute_uga_flood_recurrence.py, dev blob) as the
"predictability" backdrop, with the candidate districts from the plan page
(pages/uganda-flood-trigger/) outlined by UHF severity tier — A/B/C as an
ordinal red ramp, the untiered Teso/Kyoga scope-question group in purple —
and the largest IOM DTM affected counts labeled. A triangle marks districts
with a URCS/FAO community flood-risk hotspot map (Dec 2023).

Writes pages/uganda-flood-trigger/trigger_map.png (committed page asset).

Run:  uv run python analysis/uga_flood_trigger_map.py
"""

import io
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import ocha_stratus as stratus
import rasterio
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from ocha_stratus import codab
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

UGA_DIR = f"{PROJECT_PREFIX}/processed/uga"
OUT = Path(__file__).resolve().parent.parent / "pages/uganda-flood-trigger/trigger_map.png"

# Groups from the plan page's target-area matrix (UHF note tiers; Teso group
# untiered — "trigger-dependent Sev 2-3, verify", the open scope decision).
TIERS = {
    "A": ["Kasese", "Ntoroko"],
    "B": ["Bulambuli", "Kween", "Bukwo", "Bududa"],
    "C": ["Bundibugyo", "Bunyangabu", "Manafwa", "Namisindwa", "Sironko"],
    "T": ["Katakwi", "Pallisa", "Butaleja"],
}
COL = {"A": "#9a232a", "B": "#c65a45", "C": "#ec9a7f", "T": "#6a4c93"}

# Peak affected per district from the IOM DTM compilation (xlsx shared Aug 2026,
# sourced to published DTM EET reports). Only the five largest are labeled.
DTM_PEAK = {
    "Kasese": ("200,000", "Nov–Dec 2024"),
    "Pallisa": ("49,000", "Sep–Oct 2025"),
    "Ntoroko": ("30,224", "Aug 2024"),
    "Katakwi": ("29,735", "Sep–Oct 2025"),
    "Butaleja": ("9,098", "Apr 2024"),
}

# Districts with a URCS/FAO community flood-risk hotspot map (Dec 2023 set;
# 9 unique maps — the shared PDF duplicates Mbale where Sironko should be).
URCS = {"Bulambuli", "Butaleja", "Bundibugyo", "Kasese", "Katakwi",
        "Manafwa", "Mbale", "Ntoroko", "Namisindwa"}

# Label offsets (degrees) and leader lines, tuned by looking at the render:
# Rwenzori labels push west into DRC, the Elgon cluster fans east into Kenya.
OFFSET = {
    "Kasese": (-1.15, -0.35), "Ntoroko": (-1.20, 0.45),
    "Bundibugyo": (-1.10, 1.30), "Bunyangabu": (-1.60, -0.05),
    "Kween": (1.05, 0.75), "Bukwo": (1.05, 0.40),
    "Bulambuli": (1.10, 0.05), "Sironko": (1.10, -0.30),
    "Bududa": (1.05, -0.62), "Manafwa": (1.00, -0.95),
    "Namisindwa": (0.95, -1.30), "Butaleja": (0.70, -1.45),
    "Katakwi": (0.55, 0.85), "Pallisa": (-1.10, -1.25),
}


def main() -> None:
    cod2 = codab.load_codab_from_blob("uga", admin_level=2)
    want = [d for ds in TIERS.values() for d in ds]
    missing = set(want) - set(cod2["ADM2_EN"])
    assert not missing, f"district names not in CODAB: {missing}"

    tif = stratus.load_blob_data(f"{UGA_DIR}/flood_ond_recurrence.tif", stage="dev")
    with MemoryFile(tif) as mf, mf.open() as ds:
        rec = ds.read(1)  # recurrence of SFED >= 0.05, share of seasons
        ext = [ds.bounds.left, ds.bounds.right, ds.bounds.bottom, ds.bounds.top]
        inside = geometry_mask(cod2.geometry, out_shape=rec.shape,
                               transform=ds.transform, invert=True, all_touched=True)

    fig, ax = plt.subplots(figsize=(10.6, 9.2), dpi=200)
    ax.imshow(np.where(inside, 0, np.nan), extent=ext, cmap="Greys",
              vmin=-0.15, vmax=3, zorder=1)
    rec_m = np.ma.masked_where(~inside | (rec < 0.02), rec)
    im = ax.imshow(rec_m, extent=ext, cmap="Blues", vmin=0, vmax=1, zorder=2)
    cod2.boundary.plot(ax=ax, color="#c9d2d3", linewidth=0.35, zorder=3)
    cod2.dissolve().boundary.plot(ax=ax, color="#8a9a9c", linewidth=0.9, zorder=4)

    for tier, dists in TIERS.items():
        sel = cod2[cod2["ADM2_EN"].isin(dists)]
        sel.plot(ax=ax, facecolor=COL[tier], alpha=0.16, zorder=5)
        sel.boundary.plot(ax=ax, color=COL[tier], linewidth=2.0, zorder=6)

    for tier, dists in TIERS.items():
        for name in dists:
            geom = cod2.loc[cod2["ADM2_EN"] == name, "geometry"].iloc[0]
            cx, cy = geom.representative_point().coords[0]
            if name in URCS:
                ax.plot(cx, cy, marker="^", ms=5, mfc="#1a1a1a", mec="white",
                        mew=0.6, zorder=8)
            dx, dy = OFFSET.get(name, (0.6, 0.4))
            peak = DTM_PEAK.get(name)
            text = f"{name}\n{peak[0]} affected, {peak[1]}" if peak else name
            weight = "bold" if tier in ("A", "T") else "normal"
            ax.annotate(text, (cx, cy), xytext=(cx + dx, cy + dy), fontsize=8,
                        fontweight=weight, color="#1a1a1a", ha="center", va="center",
                        zorder=9, arrowprops=dict(arrowstyle="-", color="#8a9a9c",
                                                  lw=0.7, shrinkA=8, shrinkB=2),
                        bbox=dict(boxstyle="round,pad=0.25", fc="white",
                                  ec="#d9dfe0", lw=0.5, alpha=0.92))

    handles = [
        Patch(fc=COL["A"], alpha=0.4, ec=COL["A"], lw=2,
              label="Tier A — Severity 3 (verified sites): action window"),
        Patch(fc=COL["B"], alpha=0.4, ec=COL["B"], lw=2,
              label="Tier B — high readiness (Sev 3 on verification)"),
        Patch(fc=COL["C"], alpha=0.4, ec=COL["C"], lw=2,
              label="Tier C — conditional monitoring"),
        Patch(fc=COL["T"], alpha=0.4, ec=COL["T"], lw=2,
              label="Teso/Kyoga — scope decision pending;\nmost predictable flooding, big 2025 caseloads"),
        Line2D([], [], marker="^", ls="none", ms=6, mfc="#1a1a1a", mec="white",
               label="URCS/FAO community flood-risk map (Dec 2023)"),
    ]
    leg = ax.legend(handles=handles, loc="lower left", fontsize=8.2, frameon=True,
                    framealpha=0.94, edgecolor="#d9dfe0", borderpad=0.9,
                    title="Candidate districts (UHF note, Aug 2026)")
    leg.get_title().set_fontsize(8.6)
    leg.get_title().set_fontweight("bold")

    cax = fig.add_axes([0.86, 0.60, 0.022, 0.24])
    cb = fig.colorbar(im, cax=cax)
    cb.set_label("share of OND seasons flooded\n(FloodScan SFED ≥ 0.05, 1998–2025)",
                 fontsize=8)
    cb.ax.tick_params(labelsize=7.5)
    cb.ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")

    ax.set_title("Uganda OND 2026 flood trigger — where it can point\n", fontsize=13,
                 fontweight="bold", loc="left")
    ax.text(0, 1.005,
            "Riverine window: Ntoroko (+ Teso group if ruled in scope) · "
            "flash/landslide window: Kasese + Mt Elgon corridor",
            transform=ax.transAxes, fontsize=9, color="#555")
    ax.set_xlim(29.0, 36.3)
    ax.set_ylim(-1.7, 4.4)
    ax.set_axis_off()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
