"""Compute CMA CMME country-level (adm0) skill stats, mirroring compute_skill.py.

Forecast source: CMA CMME (China Multi-Model Ensemble) seasonal precipitation,
ensemble mean at 1°, from the ds-cma-datasharing dev blob:

  processed/CMME_history.zarr                     — hindcast, inits 1991-01…2020-12
  cma_ftp/data_out/cmme/PREC.6m.CMME.*.1x1.ens.nc — realtime, inits 2025-08 onward

Monthly leads run 1–6 (the issue month itself is not forecast), so complete
fully-forecast trimesters exist for trimester leads 1–4 only and there are no
in-season (negative-lead) combinations — those combos come out empty/None and
are excluded by the site exports. The hindcast is masked (ocean + some arid
cells); the same static mask is applied to the realtime fields so hindcast and
realtime aggregate over identical cells.

Aggregation to adm0: exact area-overlap × cos(lat) weights of each 1° cell
against the country polygons in analysis/_world_countries.gpkg (iso3 → adm0
pcode via public.polygon), then the same skill machinery as SEAS5 vs ERA5
(public.era5), including the detrended variant.

Outputs (dev blob, ds-seas5-skill/processed/cma/):
  skill_stats.parquet, paired_yearly.parquet, skill_stats_detrended.parquet,
  paired_yearly_detrended.parquet, cma_adm0_monthly.parquet

Run:  uv run python pipeline/compute_skill_cma.py
      uv run python pipeline/compute_skill_cma.py --pcodes KEN ETH --no-upload
"""

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import ocha_stratus as stratus
import pandas as pd
import xarray as xr
from shapely.geometry import box
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX
from src.skill import run_all_combinations

CMA_PREFIX = "ds-cma-datasharing"
HIND_PREFIX = f"{CMA_PREFIX}/processed/CMME_history.zarr/"
RT_PREFIX = f"{CMA_PREFIX}/cma_ftp/data_out/cmme/"
RT_RE = re.compile(r"PREC\.6m\.CMME\.(\d{6})\.1x1\.ens\.nc$")

OUT_PREFIX = f"{PROJECT_PREFIX}/processed/cma"
GEO_SRC = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"

MONTHLY_LOCAL = "cma_adm0_monthly.parquet"  # cached in --cache-dir; skips re-aggregation


def download_inputs(cache: Path) -> list[Path]:
    """Sync the hindcast zarr + realtime NetCDFs from blob into the local cache.

    Returns the (sorted) list of realtime file paths.
    """
    cc = stratus.get_container_client(stage="dev")

    blobs = list(cc.list_blobs(name_starts_with=HIND_PREFIX))
    names = {b.name for b in blobs}
    files = [
        b.name for b in blobs
        if b.size > 0 and not any(n.startswith(b.name + "/") for n in names)
    ]

    def _get_hind(name: str) -> int:
        dest = cache / "CMME_history.zarr" / name[len(HIND_PREFIX):]
        if dest.exists() and dest.stat().st_size > 0:
            return 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cc.download_blob(name).readall())
        return 1

    with ThreadPoolExecutor(8) as ex:
        n = sum(ex.map(_get_hind, files))
    tqdm.write(f"Hindcast zarr: {n} new file(s), {len(files)} total")

    rt_names = [
        b.name for b in cc.list_blobs(name_starts_with=RT_PREFIX)
        if RT_RE.search(b.name)
    ]

    def _get_rt(name: str) -> int:
        dest = cache / Path(name).name
        if dest.exists():
            return 0
        dest.write_bytes(cc.download_blob(name).readall())
        return 1

    with ThreadPoolExecutor(8) as ex:
        n = sum(ex.map(_get_rt, rt_names))
    tqdm.write(f"Realtime files: {n} new, {len(rt_names)} total")
    return sorted(cache / Path(n).name for n in rt_names)


# Known land cells (lat, lon 0–360) for the hindcast orientation check.
_LAND_PROBES = [(25, 80), (60, 100), (35, 105), (-25, 135), (0, 20)]


def _fix_lat(hind: xr.DataArray) -> xr.DataArray:
    """Fix the hindcast's inverted latitude coordinate, if present.

    The upstream CMME_history.zarr labels its rows 90…−90 while the data
    actually runs −90…90 (land/ocean probes show mirrored geography — e.g.
    India reads NaN while the southern Indian Ocean has values). Detect the
    orientation from known land cells so an eventual upstream fix won't get
    double-flipped here.
    """
    sample = hind.isel(init_time=0, lead=0)
    flipped = sample.assign_coords(lat=-sample.lat).sortby("lat")
    hits = lambda da: sum(
        not np.isnan(float(da.sel(lat=la, lon=lo))) for la, lo in _LAND_PROBES
    )
    if hits(flipped) > hits(sample):
        tqdm.write("Hindcast lat coordinate is inverted vs data — flipping.")
        return hind.assign_coords(lat=-hind.lat).sortby("lat")
    return hind


def load_cmme(cache: Path, rt_files: list[Path]) -> xr.DataArray:
    """Combined hindcast + realtime PREC[init_time, lead, lat, lon], mm/day.

    lat ascending (−90…90), lon 0…359 (cell centers on integer degrees), lead 1–6.
    The hindcast's static mask (ocean + arid cells) is applied to realtime too so
    both parts aggregate over identical cells.
    """
    hind = xr.open_zarr(cache / "CMME_history.zarr", consolidated=False)["PREC"]
    hind = _fix_lat(hind.sortby("lat").load())
    mask = np.isnan(hind.isel(init_time=0, lead=0).values)

    rt_parts = []
    for f in rt_files:
        ym = RT_RE.search(f.name).group(1)
        init = pd.Timestamp(year=int(ym[:4]), month=int(ym[4:6]), day=1)
        if init <= pd.Timestamp(hind.init_time.values[-1]):
            continue  # already in the hindcast
        ds = xr.open_dataset(f, decode_times=False)
        da = ds["PREC"].sortby("lat")
        # time coord = months since issue (1…6) — rename to lead, stamp the init.
        da = da.rename({"time": "lead"}).assign_coords(lead=da["time"].values.astype(int))
        da = da.where(~xr.DataArray(mask, coords={"lat": da.lat, "lon": da.lon}))
        rt_parts.append(da.expand_dims(init_time=[init]).astype("float32"))

    full = xr.concat([hind] + rt_parts, dim="init_time", coords="minimal", join="exact")
    tqdm.write(
        f"CMME combined: {full.sizes['init_time']} inits "
        f"({pd.Timestamp(full.init_time.values[0]):%Y-%m}…"
        f"{pd.Timestamp(full.init_time.values[-1]):%Y-%m}), leads {list(full.lead.values)}"
    )
    return full


def build_weights(
    geo: gpd.GeoDataFrame, mask: np.ndarray
) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Per-iso3 sparse zonal weights on the 1° grid: (lat_idx, lon_idx, weight).

    Weight = exact polygon–cell overlap area (deg²) × cos(lat). Cells under the
    CMME mask are dropped. Antimeridian-crossing countries are handled naturally
    by iterating polygon parts in their own lon range and wrapping the cell index
    (lon_idx = center mod 360).
    """
    weights: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for _, row in geo.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        acc: dict[tuple[int, int], float] = {}
        parts = geom.geoms if hasattr(geom, "geoms") else [geom]
        for part in parts:
            minx, miny, maxx, maxy = part.bounds
            for c_lon in np.arange(np.ceil(minx - 0.5), np.floor(maxx + 0.5) + 1):
                for c_lat in np.arange(
                    max(np.ceil(miny - 0.5), -90), min(np.floor(maxy + 0.5), 90) + 1
                ):
                    cell = box(c_lon - 0.5, c_lat - 0.5, c_lon + 0.5, c_lat + 0.5)
                    a = part.intersection(cell).area
                    if a <= 0:
                        continue
                    key = (int(c_lat) + 90, int(c_lon) % 360)
                    acc[key] = acc.get(key, 0.0) + a * np.cos(np.deg2rad(c_lat))
        # Drop cells under the CMME mask.
        kept = {k: w for k, w in acc.items() if not mask[k[0], k[1]]}
        if not kept:
            continue
        idx = np.array(list(kept.keys()), dtype=int)
        weights[row["iso3"]] = (idx[:, 0], idx[:, 1], np.array(list(kept.values())))
    return weights


def aggregate_adm0(
    da: xr.DataArray,
    weights: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    iso3_to_pcode: dict[str, str],
) -> pd.DataFrame:
    """Weighted adm0 means → long DataFrame[pcode, iso3, issued_date, valid_date, mean]."""
    vals = da.values  # (init, lead, lat, lon)
    inits = pd.DatetimeIndex(da.init_time.values)
    leads = da.lead.values.astype(int)
    periods = pd.PeriodIndex(inits, freq="M")

    frames = []
    for iso3, (li, lj, w) in weights.items():
        pcode = iso3_to_pcode.get(iso3)
        if pcode is None:
            continue
        v = vals[:, :, li, lj]  # (init, lead, ncell)
        valid = ~np.isnan(v)
        den = (w * valid).sum(axis=2)
        with np.errstate(invalid="ignore"):
            mean = np.where(den > 0, np.nansum(v * w, axis=2) / den, np.nan)
        for k, lead in enumerate(leads):
            frames.append(pd.DataFrame({
                "pcode": pcode,
                "iso3": iso3,
                "issued_date": inits,
                "valid_date": (periods + lead).to_timestamp(),
                "leadtime": lead,
                "mean": mean[:, k],
            }))
    df = pd.concat(frames, ignore_index=True).dropna(subset=["mean"])
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="/tmp/cmme_cache",
                        help="Local cache for the CMME blob inputs (~200 MB)")
    parser.add_argument("--pcodes", nargs="+", metavar="PCODE",
                        help="Only compute these adm0 pcodes (skill parquets are NOT "
                             "merged with existing blob data — use for testing)")
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip the blob uploads (local dry run)")
    parser.add_argument("--reaggregate", action="store_true",
                        help="Ignore the cached adm0 monthly parquet and re-aggregate")
    args = parser.parse_args()

    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    engine = stratus.get_engine("prod")
    with engine.connect() as conn:
        df_adm0 = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=0", conn
        )
    iso3_to_pcode = df_adm0.set_index("iso3")["pcode"].to_dict()

    monthly_path = cache / MONTHLY_LOCAL
    if monthly_path.exists() and not args.reaggregate:
        tqdm.write(f"Using cached adm0 monthly series: {monthly_path}")
        df_cma = pd.read_parquet(monthly_path)
    else:
        rt_files = download_inputs(cache)
        da = load_cmme(cache, rt_files)
        mask = np.isnan(da.isel(init_time=0, lead=0).values)
        geo = gpd.read_file(GEO_SRC)[["iso3", "geometry"]]
        tqdm.write(f"Building zonal weights for {len(geo)} country polygons...")
        weights = build_weights(geo, mask)
        no_cells = sorted(set(geo["iso3"]) - set(weights))
        no_pcode = sorted(set(weights) - set(iso3_to_pcode))
        if no_cells:
            tqdm.write(f"  no unmasked CMME cells ({len(no_cells)}): {no_cells}")
        if no_pcode:
            tqdm.write(f"  no adm0 pcode for iso3 ({len(no_pcode)}): {no_pcode}")
        df_cma = aggregate_adm0(da, weights, iso3_to_pcode)
        df_cma.to_parquet(monthly_path, index=False)
        tqdm.write(f"Aggregated {df_cma['pcode'].nunique()} countries "
                   f"({len(df_cma):,} rows) -> {monthly_path}")

    pcodes = args.pcodes or sorted(df_cma["pcode"].unique())
    df_adm0 = df_adm0[df_adm0["pcode"].isin(pcodes)]

    tqdm.write(f"Querying ERA5 for {len(df_adm0)} pcodes...")
    ph = ",".join(["%s"] * len(df_adm0))
    with engine.connect() as conn:
        df_era5_all = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
            conn, params=tuple(df_adm0["pcode"]), parse_dates=["valid_date"],
        )

    all_skill, all_paired, all_skill_dt, all_paired_dt = [], [], [], []
    for _, row in tqdm(df_adm0.iterrows(), total=len(df_adm0), desc="pcodes"):
        pcode, iso3, country_name = row["pcode"], row["iso3"], row["name"]
        df_fc = df_cma[df_cma["pcode"] == pcode]
        df_obs = df_era5_all[df_era5_all["pcode"] == pcode]
        if df_fc.empty or df_obs.empty:
            continue
        df_skill, df_paired = run_all_combinations(
            pcode, iso3, country_name, df_fc, df_obs
        )
        df_skill_dt, df_paired_dt = run_all_combinations(
            pcode, iso3, country_name, df_fc, df_obs, detrend=True
        )
        all_skill.append(df_skill)
        all_paired.append(df_paired)
        all_skill_dt.append(df_skill_dt)
        all_paired_dt.append(df_paired_dt)

    outputs = {
        f"{OUT_PREFIX}/skill_stats.parquet": pd.concat(all_skill, ignore_index=True),
        f"{OUT_PREFIX}/paired_yearly.parquet": pd.concat(all_paired, ignore_index=True),
        f"{OUT_PREFIX}/skill_stats_detrended.parquet": pd.concat(all_skill_dt, ignore_index=True),
        f"{OUT_PREFIX}/paired_yearly_detrended.parquet": pd.concat(all_paired_dt, ignore_index=True),
        f"{OUT_PREFIX}/cma_adm0_monthly.parquet": df_cma,
    }
    for blob, frame in outputs.items():
        if args.no_upload:
            local = cache / Path(blob).name
            frame.to_parquet(local, index=False)
            tqdm.write(f"[no-upload] {blob}: {len(frame):,} rows -> {local}")
        else:
            tqdm.write(f"Saving {blob} ({len(frame):,} rows)")
            stratus.upload_parquet_to_blob(frame, blob, stage="dev")
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
