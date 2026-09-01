"""Major historical flood events map for the Uganda flood-trigger plan page.

Companion to uga_flood_trigger_map.py (same extent, backdrop and styling):
the worst flood / wet mass-movement events in EM-DAT's Uganda record
(2001-2024, selected by affected or deaths), placed at the centroid of the
districts named in each event's Location field (CODAB adm2 match, the
uganda_hnrp.qmd approach; manual override where the record is region-level).
Marker SHAPE is the EM-DAT modality (riverine / flash / landslide / flood
unspecified), marker SIZE the affected count, and a gold ring marks the two
events CERF responded to (Oct 2007 ~$4.8M RR; Jan 2020 ~$4.0M RR for the
Nov-Dec 2019 floods/landslides — CERF Allocations dataset, HDX).

Writes pages/uganda-flood-trigger/events_map.png (committed page asset).

Run:  uv run python analysis/uga_flood_events_map.py
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import ocha_stratus as stratus
from matplotlib.lines import Line2D
from ocha_stratus import codab, emdat
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

UGA_DIR = f"{PROJECT_PREFIX}/processed/uga"
OUT = Path(__file__).resolve().parent.parent / "pages/uganda-flood-trigger/events_map.png"

# The events: worst of EM-DAT's 47 flood + wet mass-movement records for UGA,
# by affected or deaths. label = what the map prints; place = manual (lon, lat)
# for region-level records, else centroid of the districts EM-DAT names.
MODALITY = {"Riverine flood": "riverine", "Flash flood": "flash",
            "Landslide (wet)": "landslide", "Mudslide": "landslide",
            "Flood (General)": "unspecified"}
EVENTS = [
    # disno, label, cerf ($ text or None), manual place or None, label offset
    ("2007-0408-UGA", "Aug–Oct 2007 · 718k affected", "CERF $4.8M", (33.55, 2.05), (-0.1, 1.0)),
    ("2019-0625-UGA", "Nov–Dec 2019 · 65k aff., 65 deaths", "CERF $4.0M", (30.35, 0.90), (-1.45, 0.60)),
    ("2019-0599-UGA", "Nov–Dec 2019 · Bundibugyo", None, (30.05, 0.72), (-1.45, 0.05)),
    ("2020-0182-UGA", "May 2020 · 100k affected", None, (34.52, 1.44), (0.95, 0.95)),
    ("2020-0213-UGA", "May 2020 · Kasese, 25k", None, (30.00, 0.32), (-1.35, -0.42)),
    ("2013-0197-UGA", "May 2013 · Kasese, 25k", None, (30.12, 0.05), (-1.30, -0.95)),
    ("2011-0376-UGA", "Aug–Sep 2011 · 63k affected", None, (34.32, 1.22), (1.15, 0.35)),
    ("2008-0527-UGA", "Nov 2008 · 30k aff., 49 deaths", None, (31.85, 3.35), (-1.25, 0.55)),
    ("2010-0084-UGA", "Feb–Mar 2010 · Bududa, 388 deaths", None, (34.25, 0.88), (1.30, -0.55)),
    ("2019-0227-UGA", "Jun 2019 · 130k aff., 61 deaths", None, (34.40, 1.02), (1.20, -0.10)),
    ("2024-0883-UGA", "Nov 2024 · Bulambuli, 141 deaths", None, (34.35, 1.35), (0.90, 1.45)),
    ("2022-0481-UGA", "Jul–Aug 2022 · Mbale, 78k, 32 deaths", None, (34.15, 1.05), (0.60, -1.30)),
    ("2021-0240-UGA", "May 2021 · Butaleja, 75k", None, (33.94, 0.90), (-0.35, -1.35)),
    ("2024-0231-UGA", "Apr–May 2024 · 58k aff., 77 deaths\n(countrywide)", None, (32.30, 0.65), (-0.65, -0.95)),
]
MARKER = {"riverine": "o", "flash": "D", "landslide": "v", "unspecified": "s"}
MNAME = {"riverine": "Riverine flood", "flash": "Flash flood",
         "landslide": "Landslide / mudslide", "unspecified": "Flood, subtype unrecorded"}


def size_for(affected: float) -> float:
    if not np.isfinite(affected):
        return 55.0
    return float(np.clip(28 + 32 * np.log10(max(affected, 1e3) / 1e3), 40, 190))


def main() -> None:
    cod2 = codab.load_codab_from_blob("uga", admin_level=2)
    em = emdat.load_emdat_from_blob(iso3="UGA").set_index("DisNo.")

    tif = stratus.load_blob_data(f"{UGA_DIR}/flood_ond_recurrence.tif", stage="dev")
    with MemoryFile(tif) as mf, mf.open() as ds:
        rec = ds.read(1)
        ext = [ds.bounds.left, ds.bounds.right, ds.bounds.bottom, ds.bounds.top]
        inside = geometry_mask(cod2.geometry, out_shape=rec.shape,
                               transform=ds.transform, invert=True, all_touched=True)

    fig, ax = plt.subplots(figsize=(10.6, 9.2), dpi=200)
    ax.imshow(np.where(inside, 0, np.nan), extent=ext, cmap="Greys",
              vmin=-0.15, vmax=3, zorder=1)
    rec_m = np.ma.masked_where(~inside | (rec < 0.02), rec)
    ax.imshow(rec_m, extent=ext, cmap="Blues", vmin=0, vmax=1, alpha=0.55, zorder=2)
    cod2.boundary.plot(ax=ax, color="#c9d2d3", linewidth=0.35, zorder=3)
    cod2.dissolve().boundary.plot(ax=ax, color="#8a9a9c", linewidth=0.9, zorder=4)

    for disno, label, cerf, place, (dx, dy) in EVENTS:
        row = em.loc[disno]
        mod = MODALITY[row["Disaster Subtype"]]
        x, y = place
        s = size_for(row["Total Affected"])
        if cerf:
            ax.scatter(x, y, s=s * 2.6, marker="o", facecolor="none",
                       edgecolor="#b8860b", linewidth=1.8, zorder=6)
        ax.scatter(x, y, s=s, marker=MARKER[mod], facecolor="#2b3a3c",
                   edgecolor="white", linewidth=0.9, zorder=7)
        text = f"{label}\n{cerf}" if cerf else label
        weight = "bold" if cerf else "normal"
        ax.annotate(text, (x, y), xytext=(x + dx, y + dy), fontsize=7.8,
                    fontweight=weight, color="#1a1a1a", ha="center", va="center",
                    zorder=9, arrowprops=dict(arrowstyle="-", color="#8a9a9c",
                                              lw=0.7, shrinkA=10, shrinkB=3),
                    bbox=dict(boxstyle="round,pad=0.25", fc="white",
                              ec="#d9dfe0", lw=0.5, alpha=0.92))

    handles = [Line2D([], [], marker=MARKER[m], ls="none", ms=8, mfc="#2b3a3c",
                      mec="white", label=MNAME[m])
               for m in ("riverine", "flash", "landslide", "unspecified")]
    handles += [
        Line2D([], [], marker="o", ls="none", ms=13, mfc="none", mec="#b8860b",
               mew=1.8, label="CERF rapid-response allocation"),
        Line2D([], [], marker="o", ls="none", ms=5, mfc="#2b3a3c", mec="white",
               label="marker size — people affected"),
    ]
    leg = ax.legend(handles=handles, loc="lower left", fontsize=8.2, frameon=True,
                    framealpha=0.94, edgecolor="#d9dfe0", borderpad=0.9,
                    title="Worst EM-DAT events 2001–2024, by modality")
    leg.get_title().set_fontsize(8.6)
    leg.get_title().set_fontweight("bold")

    ax.set_title("Uganda — major flood events and how they flooded\n", fontsize=13,
                 fontweight="bold", loc="left")
    ax.text(0, 1.005,
            "Riverine floods drive the affected counts (Teso/Kyoga, Kasese); "
            "landslides drive the deaths (Mt Elgon) · backdrop: OND flood recurrence",
            transform=ax.transAxes, fontsize=9, color="#555")
    ax.set_xlim(29.0, 36.3)
    ax.set_ylim(-1.7, 4.4)
    ax.set_axis_off()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
