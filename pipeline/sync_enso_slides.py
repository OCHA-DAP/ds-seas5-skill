"""Sync the rendered ENSO slides between the local repo and the dev blob.

The slides (~700 MB of per-country SVGs + PDFs + countries.json) are too heavy
to live in git, so pages/enso/slides/ is gitignored and the bundle lives in the
dev `projects` container under {PROJECT_PREFIX}/processed/enso_slides/. The
Pages deploy workflow runs `download` to place them into the site artifact
(modality (a) of the KB's static-data-apps page, with blob as the durable
store); after re-rendering locally, run `upload` to publish a new bundle.

    uv run python pipeline/sync_enso_slides.py upload
    uv run python pipeline/sync_enso_slides.py download [--dest DIR]

Upload needs DSCI_AZ_BLOB_DEV_SAS_WRITE; download only the read SAS
(DSCI_AZ_BLOB_DEV_SAS), which the repo's Actions secrets hold.
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ocha_stratus as stratus

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.constants import PROJECT_PREFIX  # noqa: E402

BLOB_PREFIX = f"{PROJECT_PREFIX}/processed/enso_slides/"
LOCAL = HERE.parent / "pages" / "enso" / "slides"
MANIFEST_LOCAL = HERE.parent / "pages" / "enso" / "countries.json"
WORKERS = 12


def _files_to_upload() -> list[Path]:
    files = sorted(p for p in LOCAL.iterdir()
                   if p.suffix in (".svg", ".pdf") and p.is_file())
    if not files:
        raise SystemExit(f"nothing to upload in {LOCAL}")
    return files + [MANIFEST_LOCAL]


def upload() -> None:
    files = _files_to_upload()
    # bundle manifest: names + sizes + hashes, so download can verify and prune
    bundle = {
        f.name: {"bytes": f.stat().st_size,
                 "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        for f in files
    }
    print(f"Uploading {len(files)} files "
          f"({sum(v['bytes'] for v in bundle.values()) / 1e6:.0f} MB) "
          f"to dev:projects/{BLOB_PREFIX}")

    def _up(f: Path) -> str:
        stratus.upload_blob_data(f.read_bytes(), f"{BLOB_PREFIX}{f.name}",
                                 stage="dev", container_name="projects")
        return f.name

    with ThreadPoolExecutor(WORKERS) as ex:
        for i, name in enumerate(ex.map(_up, files), 1):
            if i % 100 == 0:
                print(f"  {i}/{len(files)}")
    stratus.upload_blob_data(json.dumps(bundle).encode(),
                             f"{BLOB_PREFIX}_bundle.json",
                             stage="dev", container_name="projects")
    print("upload done (bundle manifest written)")


def download(dest: Path) -> None:
    bundle = json.loads(stratus.load_blob_data(
        f"{BLOB_PREFIX}_bundle.json", stage="dev",
        container_name="projects"))
    dest.mkdir(parents=True, exist_ok=True)
    names = sorted(bundle)
    print(f"Downloading {len(names)} files "
          f"({sum(v['bytes'] for v in bundle.values()) / 1e6:.0f} MB)")

    def _down(name: str) -> None:
        data = stratus.load_blob_data(f"{BLOB_PREFIX}{name}", stage="dev",
                                      container_name="projects")
        if hashlib.sha256(data).hexdigest() != bundle[name]["sha256"]:
            raise RuntimeError(f"{name}: hash mismatch")
        out = dest / ("countries.json" if name == "countries.json" else name)
        out.write_bytes(data)

    with ThreadPoolExecutor(WORKERS) as ex:
        for i, _ in enumerate(ex.map(_down, names), 1):
            if i % 100 == 0:
                print(f"  {i}/{len(names)}")
    # countries.json belongs one level up from the slides dir on the site
    cj = dest / "countries.json"
    if cj.exists():
        cj.replace(dest.parent / "countries.json")
    print(f"download done -> {dest}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["upload", "download"])
    ap.add_argument("--dest", type=Path, default=LOCAL,
                    help="download target dir (default: pages/enso/slides)")
    args = ap.parse_args()
    if args.action == "upload":
        upload()
    else:
        download(args.dest)
