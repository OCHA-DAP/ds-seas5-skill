"""Pixel-level (raster) version of the country-level skill method in ``src/skill.py``.

Each routine mirrors its scalar counterpart in ``src.skill`` but operates on xarray
DataArrays with a ``season_year`` dimension that is reduced over, broadcasting across the
``(y, x)`` grid. SEAS5 is provided as a ``(date, leadtime, y, x)`` array where ``date`` is the
*issued* month (1st of month) and ``leadtime`` is months ahead; ERA5 as ``(date, y, x)`` where
``date`` is the *valid* month.

See ``src/skill.py`` for the authoritative formulas; this module keeps them identical, just
vectorised over pixels.
"""

import numpy as np
import pandas as pd
import xarray as xr
from scipy.stats import norm

from src.constants import MIN_YEARS, TRIMESTERS  # noqa: F401  (TRIMESTERS re-exported for callers)
from src.skill import season_year_for, trimester_lead  # noqa: F401  (trimester_lead re-exported)


# ── Trimester aggregation ────────────────────────────────────────────────────────────
def _valid_month_for_leadtime(issued_month: int, leadtime):
    return ((issued_month - 1 + leadtime) % 12) + 1


def _dt_coord(da: xr.DataArray) -> xr.DataArray:
    """stack_cogs stores `date` as strings; coerce to datetime64 so `.dt`/groupby work."""
    return da.assign_coords(date=pd.to_datetime(da["date"].values))


def _assign_season_year_np(dates: pd.DatetimeIndex, valid_months: list[int]) -> np.ndarray:
    """Vector version of src.skill._assign_season_year (wrapping trimesters anchor on >month 6)."""
    is_wrapping = 1 in valid_months and 12 in valid_months
    years = dates.year.to_numpy()
    if is_wrapping:
        return np.where(dates.month.to_numpy() > 6, years, years - 1)
    return years


def aggregate_seas5_trimester_grid(da_seas5: xr.DataArray, issued_month: int,
                                   valid_months: list[int]) -> xr.DataArray | None:
    """SEAS5 -> (season_year, y, x) trimester forecast mean for one issued month.

    Mirrors aggregate_seas5_trimester: filter to this issued month and the leadtimes whose
    valid month falls in the trimester, then average the (up to 3) monthly forecasts.
    """
    da_seas5 = _dt_coord(da_seas5)
    dmask = (da_seas5["date"].dt.month == issued_month).to_numpy()
    if not dmask.any():
        return None
    sub = da_seas5.isel(date=dmask)
    vlm = _valid_month_for_leadtime(issued_month, sub["leadtime"])
    lmask = vlm.isin(valid_months).to_numpy()
    if not lmask.any():
        return None
    sub = sub.isel(leadtime=lmask)
    da = sub.mean("leadtime")  # trimester mean = mean over the selected monthly leadtimes
    issued_years = pd.to_datetime(sub["date"].to_numpy()).year
    sy = np.array([season_year_for(issued_month, int(y), valid_months) for y in issued_years])
    da = da.assign_coords(season_year=("date", sy)).swap_dims({"date": "season_year"})
    da = da.drop_vars([c for c in ("date", "leadtime") if c in da.coords], errors="ignore")
    return da.sortby("season_year")


def _monthly_series_grid(da: xr.DataArray, month: int, valid_months: list[int],
                         season_years: np.ndarray | None = None) -> xr.DataArray | None:
    """One calendar month of a (date, y, x) array as a (season_year, y, x) series.

    `season_years` overrides the season_year assignment (used for SEAS5 forecast months,
    whose season_year derives from the *valid* date, not the issued `date` coord).
    """
    da = _dt_coord(da)
    mask = (da["date"].dt.month == month).to_numpy()
    if not mask.any():
        return None
    sub = da.isel(date=mask)
    sy = (season_years if season_years is not None
          else _assign_season_year_np(pd.to_datetime(sub["date"].to_numpy()), valid_months))
    sub = sub.assign_coords(season_year=("date", sy)).swap_dims({"date": "season_year"})
    sub = sub.drop_vars([c for c in ("date", "leadtime") if c in sub.coords], errors="ignore")
    return sub.sortby("season_year")


def aggregate_mixed_trimester_grid(da_seas5: xr.DataArray, da_era5: xr.DataArray,
                                   issued_month: int,
                                   valid_months: list[int]) -> xr.DataArray | None:
    """Grid version of src.skill.aggregate_mixed_trimester (in-season / negative leads).

    Months before the issue month come from ERA5 (already observed at issuance); the issue
    month and later come from this issuance's SEAS5, each bias-corrected per pixel against
    ERA5 for that calendar month (mean/std matched in log space over the overlap years)
    before the three months are averaged. Returns (season_year, y, x) in original units
    (mm/day) — the same contract as aggregate_seas5_trimester_grid.
    """
    monthly: list[xr.DataArray] = []
    for m in valid_months:
        o = (m - issued_month) % 12
        signed = o if o <= 6 else o - 12
        e_ser = _monthly_series_grid(da_era5, m, valid_months)
        if e_ser is None:
            return None
        if signed < 0:  # observed month
            monthly.append(e_ser)
            continue
        # Forecast month: SEAS5 horizon < 12 months, so (issued month, valid month)
        # uniquely determines the leadtime.
        s = _dt_coord(da_seas5)
        dmask = (s["date"].dt.month == issued_month).to_numpy()
        if not dmask.any():
            return None
        sub = s.isel(date=dmask)
        lmask = (_valid_month_for_leadtime(issued_month, sub["leadtime"]) == m).to_numpy()
        if not lmask.any():
            return None
        sub = sub.isel(leadtime=lmask).mean("leadtime")
        valid_dates = pd.DatetimeIndex(
            pd.to_datetime(sub["date"].to_numpy()) + pd.DateOffset(months=signed)
        )
        f_sy = _assign_season_year_np(valid_dates, valid_months)
        f_ser = _monthly_series_grid(sub, issued_month, valid_months, season_years=f_sy)
        if f_ser is None:
            return None
        # Per-pixel, per-month bias correction in log space over the overlap years.
        f_log = log1p(f_ser)
        e_log = log1p(e_ser)
        yrs = np.intersect1d(f_ser["season_year"].to_numpy(), e_ser["season_year"].to_numpy())
        if len(yrs) >= 2:
            fo, eo = f_log.sel(season_year=yrs), e_log.sel(season_year=yrs)
            f_mu, f_sd = fo.mean("season_year"), fo.std("season_year", ddof=1)
            e_mu, e_sd = eo.mean("season_year"), eo.std("season_year", ddof=1)
            f_sd = f_sd.where(f_sd > 1e-9, 1e-9)
            f_ser = np.expm1((f_log - f_mu) / f_sd * e_sd + e_mu).clip(min=0)
        monthly.append(f_ser)

    # Complete trimesters only: keep season_years present in all three monthly series.
    yrs = monthly[0]["season_year"].to_numpy()
    for p in monthly[1:]:
        yrs = np.intersect1d(yrs, p["season_year"].to_numpy())
    if len(yrs) == 0:
        return None
    stacked = xr.concat([p.sel(season_year=yrs) for p in monthly], dim="_slot")
    return stacked.mean("_slot", skipna=False).sortby("season_year")


def aggregate_era5_trimester_grid(da_era5: xr.DataArray,
                                  valid_months: list[int]) -> xr.DataArray | None:
    """ERA5 -> (season_year, y, x) trimester observed mean (complete 3-month seasons only)."""
    da_era5 = _dt_coord(da_era5)
    mmask = da_era5["date"].dt.month.isin(valid_months).to_numpy()
    if not mmask.any():
        return None
    sub = da_era5.isel(date=mmask)
    sy = _assign_season_year_np(pd.to_datetime(sub["date"].to_numpy()), valid_months)
    counts = pd.Series(sy).value_counts()
    complete = set(counts[counts == len(valid_months)].index)
    keep = np.isin(sy, list(complete))
    if not keep.any():
        return None
    sub = sub.isel(date=keep).assign_coords(season_year=("date", sy[keep]))
    out = sub.groupby("season_year").mean("date")
    return out.sortby("season_year")


# ── Transform / normalise / detrend ──────────────────────────────────────────────────
def log1p(da: xr.DataArray) -> xr.DataArray:
    return np.log1p(da.clip(min=0))


def _overlap_years(fc: xr.DataArray, obs: xr.DataArray) -> np.ndarray:
    return np.intersect1d(fc["season_year"].to_numpy(), obs["season_year"].to_numpy())


def normalize_seas5_grid(fc: xr.DataArray, obs: xr.DataArray) -> xr.DataArray:
    """Per-pixel: scale SEAS5 to ERA5 mean/std over the overlap years; apply to all fc years."""
    yrs = _overlap_years(fc, obs)
    if len(yrs) < 2:
        return fc
    f = fc.sel(season_year=yrs)
    o = obs.sel(season_year=yrs)
    s_mean, s_std = f.mean("season_year"), f.std("season_year", ddof=1)
    e_mean, e_std = o.mean("season_year"), o.std("season_year", ddof=1)
    s_std = s_std.where(s_std > 1e-9, 1e-9)
    return (fc - s_mean) / s_std * e_std + e_mean


def detrend_grid(da: xr.DataArray, hist_years: np.ndarray) -> xr.DataArray:
    """Per-pixel: remove the linear trend fit on hist_years, keep each pixel's hist mean."""
    h = da.sel(season_year=hist_years)
    coeffs = h.polyfit("season_year", 1, skipna=True)["polyfit_coefficients"]
    trend_all = xr.polyval(da["season_year"], coeffs)
    return da - trend_all + h.mean("season_year")


# ── Skill + forecast-position metrics ────────────────────────────────────────────────
def compute_skill_grid(fc_norm: xr.DataArray, obs: xr.DataArray):
    """Pearson r, RMSE, n_years over overlap. Returns None if < MIN_YEARS overlap years."""
    yrs = _overlap_years(fc_norm, obs)
    if len(yrs) < MIN_YEARS:
        return None
    f = fc_norm.sel(season_year=yrs)
    o = obs.sel(season_year=yrs)
    r = xr.corr(f, o, dim="season_year")
    rmse = np.sqrt(((o - f) ** 2).mean("season_year"))
    return {"pearson_r": r, "rmse": rmse, "n_years": int(len(yrs)), "overlap_years": yrs}


def _empirical_rp_grid(current: xr.DataArray, hist: xr.DataArray,
                       higher_is_more_extreme: bool) -> xr.DataArray:
    """Vectorised Weibull empirical return period (see src.skill.empirical_rp)."""
    n = hist.sizes["season_year"]
    if higher_is_more_extreme:
        rank = (hist > current).sum("season_year") + 1
    else:
        rank = (hist < current).sum("season_year") + 1
    return (n + 1) / rank


def forecast_metrics_grid(fc_norm: xr.DataArray, obs: xr.DataArray, r: xr.DataArray,
                          era5_mean: xr.DataArray, era5_std: xr.DataArray,
                          overlap_years: np.ndarray) -> dict:
    """Latest-forecast position metrics per pixel: percentile, drought/flood RP, P(lower tercile)."""
    cur_year = int(fc_norm["season_year"].max())
    current = fc_norm.sel(season_year=cur_year)             # (y, x)
    hist_F = fc_norm.sel(season_year=overlap_years)         # (overlap, y, x)
    n = len(overlap_years)

    T_log = obs.quantile(1 / 3, dim="season_year").drop_vars("quantile")

    pct = 100.0 * (hist_F <= current).sum("season_year") / n
    forecast_rp = _empirical_rp_grid(current, hist_F, higher_is_more_extreme=False)
    flood_rp = _empirical_rp_grid(current, hist_F, higher_is_more_extreme=True)

    # Empirical regression sigma over the overlap, then P(below lower tercile)
    E_hat = (1 - r) * era5_mean + r * hist_F
    sigma = (obs.sel(season_year=overlap_years) - E_hat).std("season_year", ddof=1)
    sigma_use = sigma.where(sigma > 1e-9, 1e-9)
    mu_current = (1 - r) * era5_mean + r * current
    prob = xr.apply_ufunc(lambda T, mu, s: norm.cdf(T, loc=mu, scale=s),
                          T_log, mu_current, sigma_use,
                          dask="parallelized", output_dtypes=[float])

    return {
        "current_forecast_year": cur_year,
        "current_forecast_mean": current,
        "forecast_percentile": pct,
        "forecast_rp": forecast_rp,
        "flood_rp": flood_rp,
        "prob_lower_tercile": prob,
        "lower_tercile_mm": np.expm1(T_log),
        "sigma": sigma,
    }


# ── Rainy-season mask (per pixel) ────────────────────────────────────────────────────
def monthly_clim_grid(da_era5: xr.DataArray) -> xr.DataArray:
    """ERA5 -> (month, y, x) climatological monthly mean precipitation."""
    return _dt_coord(da_era5).groupby("date.month").mean("date")


def rainy_grid(clim: xr.DataArray, valid_months: list[int],
               trimester_pct: float = 0.25, month_pct: float = 0.0) -> xr.DataArray:
    """Per-pixel rainy-trimester mask (mirrors compute_rainy_set; defaults match the app)."""
    annual = clim.sum("month")
    annual = annual.where(annual > 1e-9)
    tri = clim.sel(month=valid_months)
    tri_ok = (3 * tri.mean("month") / annual) >= trimester_pct
    month_ok = (tri / annual).min("month") >= month_pct
    return tri_ok & month_ok


def rainy_from_cube(ds: xr.Dataset, trimester_pct: float = 0.15) -> xr.DataArray:
    """Re-derive the per-(trimester, y, x) rainy mask from a skill cube at any threshold.

    Avoids reloading/regridding ERA5: each month sits in exactly 3 of the 12 overlapping
    trimesters, so the annual mean equals the sum of the 12 trimester means. Uses the cube's
    `era5_mean` (mean log1p obs; identical across issued months). Matches the baked 0.25 mask
    to ~99% of land pixels — the small difference is log-mean vs arithmetic-mean climatology.
    """
    tri_mm = np.expm1(ds["era5_mean"].mean("issued_month"))   # (trimester, y, x), mm/day
    annual = tri_mm.sum("trimester").where(lambda a: a > 1e-9)
    return (3 * tri_mm / annual) >= trimester_pct
