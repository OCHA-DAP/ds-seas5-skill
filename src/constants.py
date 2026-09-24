import calendar

PROJECT_PREFIX = "ds-seas5-skill"

MIN_YEARS: int = 10

# First year of the SEAS5 record (reforecast/hindcast start). Used to populate the
# historical issued-year selector; the record is continuous from here to the present.
SEAS5_FORECAST_START_YEAR: int = 1981

TRIMESTERS: dict[str, list[int]] = {
    "JFM": [1, 2, 3],
    "FMA": [2, 3, 4],
    "MAM": [3, 4, 5],
    "AMJ": [4, 5, 6],
    "MJJ": [5, 6, 7],
    "JJA": [6, 7, 8],
    "JAS": [7, 8, 9],
    "ASO": [8, 9, 10],
    "SON": [9, 10, 11],
    "OND": [10, 11, 12],
    "NDJ": [11, 12, 1],
    "DJF": [12, 1, 2],
}


# Calendar days per trimester in a non-leap year: the mm/day -> seasonal-total factor
# every mm export uses (HNRP tab, HDX signal tables). Feb trimesters are 1 day short in
# leap season_years (~1.1 %), accepted so the same normal reads the same everywhere.
TRIMESTER_DAYS: dict[str, int] = {
    t: sum(calendar.monthrange(2001, m)[1] for m in months) for t, months in TRIMESTERS.items()
}
