"""Cross-check that every site payload shows the forecast it CLAIMS to show.

Vintage errors are the worst failure mode this site has: data from the wrong
issuance (or a silent fallback to last year) doesn't look broken — it looks
like a precipitation anomaly. Two real incidents motivated this gate
(Aug 2026): the raster cube's in-season combos silently fell back to the
Aug-2025 issuance when ERA5 July was late, and a botched commit shipped July
pixels under an August-labelled site. Neither was detectable by eye.

Checks (exit 1 on any failure):
1. forecast.json, forecasts/index.json's latest, forecasts/<latest>.json and
   every hnrp_drought*.json agree on issued (year, month).
2. The latest issuance carries ALL expected trimesters (leads -2..4, seven of
   them) in BOTH forecast.json and forecasts/<latest>.json — a missing
   in-season trimester means the refresh ran before ERA5's elapsed month
   landed; rerun after ERA5 arrives instead of shipping without them.
3. forecast.json and forecasts/<latest>.json agree numerically (same pct for
   every country x trimester) — they are built by different exporters from
   the same blob and must never diverge.
4. Raster meta (manual pipeline, may legitimately lag): WARN if it is a whole
   issuance behind (the site disables Pixel mode for a stale raster); FAIL if
   it claims the current issuance but is missing PNG files for its own
   trimester list, or lists a trimester outside the expected set.

Run:  uv run python pipeline/verify_site_data.py            # CI: after exports, before commit
      uv run python pipeline/verify_site_data.py --strict-raster   # after a raster refresh
"""

import argparse
import json
import sys
from pathlib import Path

DOCS = Path(__file__).parent.parent / "docs"
TRI_START = {"JFM": 1, "FMA": 2, "MAM": 3, "AMJ": 4, "MJJ": 5, "JJA": 6,
             "JAS": 7, "ASO": 8, "SON": 9, "OND": 10, "NDJ": 11, "DJF": 12}

failures: list[str] = []
warnings: list[str] = []


def fail(msg):
    failures.append(msg)
    print(f"FAIL: {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"WARN: {msg}")


def lead(tri, im):
    o = (TRI_START[tri] - im) % 12
    return o - 12 if o >= 10 else o


def expected_tris(im):
    return {t for t in TRI_START if -2 <= lead(t, im) <= 4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict-raster", action="store_true",
                    help="a stale raster is a failure, not a warning (use right after "
                         "the manual raster refresh)")
    args = ap.parse_args()

    fc = json.loads((DOCS / "data" / "forecast.json").read_text())
    iy, im = fc["issued_year"], fc["issued_month"]
    print(f"forecast.json: issued {fc['issued_label']} ({iy}-{im:02d})")

    idx = json.loads((DOCS / "data" / "forecasts" / "index.json").read_text())
    latest = idx["latest"]
    if (latest["year"], latest["month"]) != (iy, im):
        fail(f"forecasts/index.json latest {latest['year']}-{latest['month']:02d} "
             f"!= forecast.json {iy}-{im:02d}")

    hist_path = DOCS / "data" / "forecasts" / f"{iy}-{im:02d}.json"
    if not hist_path.exists():
        fail(f"{hist_path.name} missing")
        hist = None
    else:
        hist = json.loads(hist_path.read_text())
        if (hist["issued_year"], hist["issued_month"]) != (iy, im):
            fail(f"{hist_path.name} claims {hist['issued_year']}-{hist['issued_month']:02d}")

    # 2. Trimester completeness — the in-season ones vanish exactly when the
    # refresh raced ERA5; that must block the release, not ship quietly.
    want = expected_tris(im)
    for name, payload in [("forecast.json", fc)] + ([(hist_path.name, hist)] if hist else []):
        have = {t["key"] for t in payload["trimesters"]}
        if have != want:
            fail(f"{name} trimesters {sorted(have)} != expected {sorted(want)} "
                 f"(missing: {sorted(want - have)}) — if in-season trimesters are missing, "
                 f"ERA5's elapsed month has not landed yet; rerun the refresh after it does")

    # 3. The two country exporters must agree numerically.
    if hist:
        n_cmp = n_diff = 0
        for iso3, tris in fc["data"].items():
            for t, v in tris.items():
                hv = hist["data"].get(iso3, {}).get(t)
                if hv is None or v.get("pct") is None or hv.get("pct") is None:
                    continue
                n_cmp += 1
                if abs(v["pct"] - hv["pct"]) > 0.011:
                    n_diff += 1
                    if n_diff <= 5:
                        fail(f"pct mismatch {iso3} {t}: forecast.json {v['pct']} "
                             f"vs {hist_path.name} {hv['pct']}")
        if n_diff > 5:
            fail(f"... and {n_diff - 5} more pct mismatches (of {n_cmp} compared)")
        print(f"forecast.json vs {hist_path.name}: {n_cmp} values compared, {n_diff} mismatches")

    # 4. Raster meta: honest labelling and internal completeness.
    meta_path = DOCS / "raster" / "data" / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        r_iy, r_im = meta["issued_year"], meta["issued_month"]
        print(f"raster meta: issued {meta['issued_label']} ({r_iy}-{r_im:02d})")
        if (r_iy, r_im) != (iy, im):
            msg = (f"raster is a different issuance ({r_iy}-{r_im:02d}) than the country "
                   f"data ({iy}-{im:02d}); the site disables Pixel mode for it")
            (fail if args.strict_raster else warn)(msg)
        r_tris = {t["key"] for t in meta["trimesters"]}
        bad = r_tris - expected_tris(r_im)
        if bad:
            fail(f"raster meta lists trimesters outside its issuance's lead window: {sorted(bad)}")
        for t in sorted(r_tris):
            for v in ("masked", "all"):
                p = DOCS / "raster" / "data" / f"{t}_{v}.png"
                if not p.exists():
                    fail(f"raster meta lists {t} but {p.name} is missing")
        if args.strict_raster and r_tris != expected_tris(r_im):
            missing = expected_tris(r_im) - r_tris
            fail(f"raster missing trimesters {sorted(missing)} — stale in-season layers "
                 f"were dropped; recompute the cube once ERA5's elapsed month is available")
    else:
        warn("no raster meta.json (pixel layer never built?)")

    print(f"\n{len(failures)} failure(s), {len(warnings)} warning(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
