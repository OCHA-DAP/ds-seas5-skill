"""Build the CMA (CMME) static-site data — docs/cma/data/, adm0 only.

Mirrors export_static_site.py + export_history_site.py for the CMA CMME
forecasts computed by compute_skill_cma.py (blob: processed/cma/). CMME's
monthly leads run 1–6 (the issue month itself is not forecast), so valid
trimester leads are 1–4 and there are no in-season (negative-lead)
trimesters. Writes:

  docs/cma/data/skill_matrix.json     — per-country lead×trimester Pearson-r matrix
  docs/cma/data/forecasts/{Y}-{M}.json — one file per issuance (hindcast 1991–2020
                                         + realtime Aug 2025→), plus index.json

The /cma page reuses the main site's countries.geojson (window.SITE_GEO), so
no geometry is written here. Same freeze semantics as the SEAS5 history
export: existing issuance files are kept unless --rebuild is passed.

Access control: the repo and the Pages site are public, so every JSON written
here is AES-256-GCM encrypted with a key derived (PBKDF2-SHA256) from a shared
password — docs/cma/index.html asks for the password and decrypts in-browser
via a fetch shim. The password comes from --password or the CMA_SITE_PASSWORD
env var and is deliberately NOT stored anywhere in the repo; --plain writes
unencrypted files (local debugging only — don't commit those).

Run:  CMA_SITE_PASSWORD=... uv run python pipeline/export_cma_site.py
      CMA_SITE_PASSWORD=... uv run python pipeline/export_cma_site.py --rebuild
"""

import argparse
import calendar
import json
import os
import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # for src
sys.path.insert(0, str(HERE))         # for the sibling export modules

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from export_static_site import (  # noqa: E402
    THRESHOLDS, build_skill_matrix, compute_rainy_set, _min_signed, _tri_label,
    _tri_valid, issued_year_for_season,
)
from export_history_site import compute_metrics  # noqa: E402

OUT = HERE.parent / "docs" / "cma" / "data"

# CMME monthly leads are 1–6, so a complete 3-month trimester needs its first
# month at lead 1–4. No lead-0 month → no in-season (mixed) trimesters either.
CMA_LEADS = [1, 2, 3, 4]
MIN_LEAD, MAX_LEAD = CMA_LEADS[0], CMA_LEADS[-1]

BLOB = f"{PROJECT_PREFIX}/processed/cma"

# Encryption-at-rest for the served JSONs. MAGIC + salt + iterations must match
# the gate script in docs/cma/index.html. The salt is public by design (it only
# defeats rainbow tables); the password is the secret and lives outside the repo.
ENC_MAGIC = b"CMAENC1"
ENC_SALT = bytes.fromhex("69c3f243a3adf0585c1198d0b9c815a5")
ENC_ITERS = 600_000


def make_encryptor(password: str):
    """bytes -> MAGIC ‖ 12-byte nonce ‖ AES-256-GCM ciphertext."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=ENC_SALT, iterations=ENC_ITERS
    ).derive(password.encode())
    aes = AESGCM(key)

    def encrypt(data: bytes) -> bytes:
        nonce = os.urandom(12)
        return ENC_MAGIC + nonce + aes.encrypt(nonce, data, None)

    return encrypt


def _default_tri(valid_tris: list[str], im: int) -> str:
    """Lead-1 trimester (the earliest fully-forecast one for CMME)."""
    from src.skill import trimester_lead
    for t in valid_tris:
        if trimester_lead(im, TRIMESTERS[t]) == 1:
            return t
    return valid_tris[len(valid_tris) // 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="Rewrite ALL issuance files (default: only add missing ones)")
    ap.add_argument("--password", default=os.environ.get("CMA_SITE_PASSWORD"),
                    help="Page password (default: CMA_SITE_PASSWORD env var)")
    ap.add_argument("--plain", action="store_true",
                    help="Write unencrypted JSON (local debugging only — don't commit)")
    args = ap.parse_args()

    if args.plain:
        write_bytes = lambda data: data
    else:
        if not args.password:
            ap.error("no password: pass --password, set CMA_SITE_PASSWORD, or use --plain")
        write_bytes = make_encryptor(args.password)

    fdir = OUT / "forecasts"
    fdir.mkdir(parents=True, exist_ok=True)

    print("Loading CMA paired_yearly + skill stats (detrended)...")
    paired = stratus.load_parquet_from_blob(
        f"{BLOB}/paired_yearly_detrended.parquet", stage="dev")
    skill = stratus.load_parquet_from_blob(
        f"{BLOB}/skill_stats_detrended.parquet", stage="dev")

    pcode_to_iso3 = skill.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    r_lookup = skill.set_index(["pcode", "issued_month", "trimester"])["pearson_r"].to_dict()

    pcodes = skill["pcode"].dropna().unique().tolist()
    engine = stratus.get_engine("prod")
    ph = ",".join(["%s"] * len(pcodes))
    print(f"Querying ERA5 climatology for {len(pcodes)} pcodes...")
    with engine.connect() as conn:
        era5 = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
            conn, params=tuple(pcodes), parse_dates=["valid_date"],
        )
    monthly_clim = (
        era5.assign(month=era5["valid_date"].dt.month)
        .groupby(["pcode", "month"])["mean"].mean()
        .reset_index().rename(columns={"mean": "mean_mm_day"})
    )
    rainy_set = compute_rainy_set(monthly_clim)

    # Per-country skill matrix (CMA leads only) + climatology.
    skill_matrix = build_skill_matrix(
        skill, monthly_clim, rainy_set, pcode_to_iso3, leads=CMA_LEADS)
    sm_path = OUT / "skill_matrix.json"
    sm_path.write_bytes(write_bytes(json.dumps(skill_matrix, separators=(",", ":")).encode()))
    print(f"Wrote {sm_path}  ({sm_path.stat().st_size/1024:.1f} KB, "
          f"{len(skill_matrix['countries'])} countries)")

    # Historical issuances (hindcast + realtime), same freeze semantics as SEAS5.
    print("Computing per-year forecast metrics...")
    met = compute_metrics(paired)
    met = met[met.apply(
        lambda r: _tri_valid(TRIMESTERS[r["trimester"]], r["issued_month"],
                             MIN_LEAD, MAX_LEAD), axis=1)].copy()
    met["issued_year"] = [issued_year_for_season(sy, im, t)
                          for sy, im, t in zip(met["season_year"], met["issued_month"], met["trimester"])]
    met["iso3"] = met["pcode"].map(pcode_to_iso3)
    met = met[met["iso3"].notna()]

    years = sorted(met["issued_year"].unique().tolist())
    months_by_year: dict[str, list[int]] = {}
    n_written = n_frozen = 0
    for (iy, im), grp in met.groupby(["issued_year", "issued_month"]):
        months_by_year.setdefault(str(int(iy)), []).append(int(im))
        path = fdir / f"{int(iy)}-{int(im):02d}.json"
        if path.exists() and not args.rebuild:
            n_frozen += 1
            continue
        valid_tris = sorted(grp["trimester"].unique(),
                            key=lambda t: _min_signed(TRIMESTERS[t], im))
        data: dict[str, dict] = {}
        for _, row in grp.iterrows():
            iso3, tri = row["iso3"], row["trimester"]
            data.setdefault(iso3, {})[tri] = {
                "pct": round(float(row["pct"]), 2),
                "r": (lambda v: round(float(v), 3) if pd.notna(v) else None)(
                    r_lookup.get((row["pcode"], im, tri))),
                "rp": round(float(row["rp"]), 1),
                "rainy": (row["pcode"], tri) in rainy_set,
            }
        payload = {
            "issued_label": f"{calendar.month_name[im]} {int(iy)}",
            "issued_month": int(im),
            "issued_year": int(iy),
            "thresholds": THRESHOLDS,
            "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in valid_tris],
            "default_trimester": _default_tri(valid_tris, im),
            "data": data,
        }
        path.write_bytes(write_bytes(json.dumps(payload, separators=(",", ":")).encode()))
        n_written += 1

    for y in months_by_year:
        months_by_year[y].sort()
    latest_year = years[-1]
    latest_month = max(months_by_year[str(latest_year)])
    index = {
        "thresholds": THRESHOLDS,
        "years": years,
        "months_by_year": months_by_year,
        "latest": {"year": latest_year, "month": latest_month,
                   "file": f"{latest_year}-{latest_month:02d}"},
        "month_names": {str(m): calendar.month_name[m] for m in range(1, 13)},
    }
    (fdir / "index.json").write_bytes(write_bytes(json.dumps(index, separators=(",", ":")).encode()))
    size = sum(f.stat().st_size for f in fdir.glob("*.json")) / 1024
    print(f"Wrote {n_written} new/updated issuance file(s), froze {n_frozen} existing, "
          f"+ index.json to {fdir}  ({size:.0f} KB total)")
    print(f"Years {years[0]}–{years[-1]}; latest issuance {latest_year}-{latest_month:02d}")


if __name__ == "__main__":
    main()
