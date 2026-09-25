"""Sync the generated site data between a checkout and the dev blob.

docs/data/, docs/raster/data/ and docs/cma/data/ are generated (~100 MB, rewritten
every issuance) and gitignored — the KB's "Pages artifact storage" modality with
blob as the durable store, like the ENSO slides. The Databricks refresh job uploads
them as one versioned bundle:

    projects/ds-seas5-skill/site/<tag>/<path>      tag = the issuance shown, YYYY-MM
    projects/ds-seas5-skill/site/<tag>/_bundle.json  names + sizes + sha256 (written last)
    projects/ds-seas5-skill/site/latest.json         {"tag": ...} — switched last

and the Pages deploy (or a local checkout) downloads it, verifying every hash.
A tag is rewritten in place when the same issuance is refreshed again; pass
--tag to roll the site back to an earlier bundle.

    uv run python pipeline/sync_site_data.py upload   [--tag YYYY-MM]
    uv run python pipeline/sync_site_data.py download [--tag YYYY-MM] [--if-exists] [--no-prune]
    uv run python pipeline/sync_site_data.py list

Upload needs DSCI_AZ_BLOB_DEV_SAS_WRITE; download only the read SAS
(DSCI_AZ_BLOB_DEV_SAS), which the repo's Actions secrets hold.
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ocha_stratus as stratus

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.constants import PROJECT_PREFIX  # noqa: E402

REPO = HERE.parent
ROOTS = ("docs/data", "docs/raster/data", "docs/cma/data")
BLOB_PREFIX = f"{PROJECT_PREFIX}/site/"
CONTAINER = "projects"
WORKERS = 12


def _blob_read(name: str) -> bytes:
    return stratus.load_blob_data(f"{BLOB_PREFIX}{name}", stage="dev", container_name=CONTAINER)


def _blob_write(name: str, data: bytes) -> None:
    stratus.upload_blob_data(data, f"{BLOB_PREFIX}{name}", stage="dev", container_name=CONTAINER)


def _local_files() -> list[Path]:
    files = []
    for root in ROOTS:
        d = REPO / root
        if d.is_dir():
            files += sorted(p for p in d.rglob("*") if p.is_file() and not p.name.startswith("."))
    return files


def _default_tag() -> str:
    fc = json.loads((REPO / "docs" / "data" / "forecast.json").read_text())
    return f"{fc['issued_year']}-{fc['issued_month']:02d}"


def upload(tag: str | None) -> None:
    files = _local_files()
    if not files:
        raise SystemExit(f"nothing to upload under {ROOTS}")
    tag = tag or _default_tag()
    bundle = {
        str(f.relative_to(REPO)): {"bytes": f.stat().st_size,
                                   "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        for f in files
    }
    total = sum(v["bytes"] for v in bundle.values())
    print(f"Uploading {len(files)} files ({total / 1e6:.0f} MB) to "
          f"dev:{CONTAINER}/{BLOB_PREFIX}{tag}/")

    def _up(f: Path) -> str:
        rel = str(f.relative_to(REPO))
        _blob_write(f"{tag}/{rel}", f.read_bytes())
        return rel

    with ThreadPoolExecutor(WORKERS) as ex:
        for i, _ in enumerate(ex.map(_up, files), 1):
            if i % 100 == 0:
                print(f"  {i}/{len(files)}")
    # Manifest last, pointer last of all: a reader never sees a half-written bundle.
    _blob_write(f"{tag}/_bundle.json", json.dumps(bundle).encode())
    _blob_write("latest.json", json.dumps({
        "tag": tag, "files": len(files), "bytes": total,
        "uploaded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }).encode())
    print(f"upload done: latest -> {tag}")


def download(tag: str | None, if_exists: bool, prune: bool) -> None:
    if tag is None:
        try:
            tag = json.loads(_blob_read("latest.json"))["tag"]
        except Exception as e:  # no bundle published yet
            if if_exists:
                print(f"no site bundle on the blob yet ({type(e).__name__}); nothing to download")
                return
            raise
    bundle = json.loads(_blob_read(f"{tag}/_bundle.json"))
    names = sorted(bundle)
    print(f"Downloading bundle {tag}: {len(names)} files "
          f"({sum(v['bytes'] for v in bundle.values()) / 1e6:.0f} MB)")

    def _down(rel: str) -> None:
        data = _blob_read(f"{tag}/{rel}")
        if hashlib.sha256(data).hexdigest() != bundle[rel]["sha256"]:
            raise RuntimeError(f"{rel}: hash mismatch")
        out = REPO / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

    with ThreadPoolExecutor(WORKERS) as ex:
        for i, _ in enumerate(ex.map(_down, names), 1):
            if i % 100 == 0:
                print(f"  {i}/{len(names)}")

    if prune:
        stale = [f for f in _local_files() if str(f.relative_to(REPO)) not in bundle]
        for f in stale:
            f.unlink()
        if stale:
            print(f"pruned {len(stale)} local file(s) not in the bundle")
    print(f"download done: bundle {tag} -> {', '.join(ROOTS)}")


def list_tags() -> None:
    names = stratus.list_container_blobs(name_starts_with=BLOB_PREFIX,
                                         container_name=CONTAINER, stage="dev")
    tags = sorted({n[len(BLOB_PREFIX):].split("/")[0] for n in names
                   if n.endswith("/_bundle.json")})
    try:
        latest = json.loads(_blob_read("latest.json"))
    except Exception:
        latest = {}
    for t in tags:
        print(t, "(latest)" if t == latest.get("tag") else "")
    if latest:
        print(f"latest.json: {latest}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["upload", "download", "list"])
    ap.add_argument("--tag", help="bundle tag (YYYY-MM); upload: default = the issuance in "
                                  "forecast.json; download: default = latest.json")
    ap.add_argument("--if-exists", action="store_true",
                    help="download: exit 0 quietly when no bundle has been published yet")
    ap.add_argument("--no-prune", action="store_true",
                    help="download: keep local files that are not in the bundle")
    args = ap.parse_args()
    if args.action == "upload":
        upload(args.tag)
    elif args.action == "download":
        download(args.tag, args.if_exists, not args.no_prune)
    else:
        list_tags()
