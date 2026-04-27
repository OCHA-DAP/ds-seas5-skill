PROJECT_PREFIX = "ds-seas5-skill"

MIN_YEARS: int = 10

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

TARGET_PCODES: list[str] = ["ETH", "SOM", "SDN", "NER", "SSD"]

PCODE_NAMES: dict[str, str] = {
    "ETH": "Ethiopia",
    "SOM": "Somalia",
    "SDN": "Sudan",
    "NER": "Niger",
    "SSD": "South Sudan",
}
