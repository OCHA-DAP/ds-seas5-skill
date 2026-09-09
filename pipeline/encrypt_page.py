"""Wrap a self-contained HTML page in a password gate for the public Pages site.

The whole source document is AES-256-GCM encrypted with a key derived from a
password (PBKDF2-SHA256, 600k iterations — the same construction as the CMA
mirror in export_cma_site.py) and embedded, base64, in a small shell page that
asks for the password and decrypts in-browser (WebCrypto), then replaces the
document with the decrypted one via document.write so its scripts run.

This is a share-by-link courtesy gate, not hard security: the repo is public,
so the ciphertext is public and brute-forceable offline. Keep the plaintext
source OUT of git (analysis/_stories/ is gitignored) — a committed plaintext
copy would defeat the gate. The durable copy of the source lives on the dev
projects blob (see pages/README.md, "Hidden password-protected stories").

Run:  PAGE_PASSWORD=... uv run python pipeline/encrypt_page.py \
          analysis/_stories/chad_2026.html pages/chad-2026/index.html \
          --title "Three Alarms Over Chad"
"""

import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

ENC_MAGIC = b"PAGEENC1"
ENC_ITERS = 600_000

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex, nofollow" />
  <title>__TITLE__</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300..800;1,6..72,300..800&family=Libre+Franklin:wght@400;600&display=swap" />
  <style>
    :root { color-scheme: light; --bg: #F8F8F6; --ink: #17170F; --ink-2: #5E5B52; --rule: #D6D3C9; --clay: #B3471C; --lake: #1F6FA8; }
    @media (prefers-color-scheme: dark) { :root { color-scheme: dark; --bg: #13120F; --ink: #ECE7DB; --ink-2: #B0AB9E; --rule: #34322B; --clay: #D9672F; --lake: #4F9BD9; } }
    html, body { margin: 0; background: var(--bg); color: var(--ink); font-family: "Libre Franklin", "Helvetica Neue", Arial, sans-serif; }
    #gate { min-height: 100vh; display: flex; align-items: flex-start; justify-content: center; padding: 0 20px; }
    #gate-box { margin-top: 16vh; max-width: 440px; width: 100%; }
    .eyebrow { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--clay); font-weight: 600; }
    h1 { font-family: "Newsreader", Georgia, serif; font-weight: 500; font-size: 40px; line-height: 1.05; margin: 10px 0 14px; text-wrap: balance; }
    p { color: var(--ink-2); font-size: 15px; line-height: 1.55; margin: 0 0 18px; }
    form { display: flex; gap: 8px; }
    input { flex: 1; padding: 10px 12px; font-size: 16px; border: 1px solid var(--rule); border-radius: 3px; background: transparent; color: var(--ink); font-family: inherit; }
    input:focus-visible, button:focus-visible { outline: 2px solid var(--lake); outline-offset: 2px; }
    button { padding: 10px 18px; font-size: 15px; font-weight: 600; border: 0; border-radius: 3px; background: var(--ink); color: var(--bg); cursor: pointer; font-family: inherit; }
    button[disabled] { opacity: .6; cursor: default; }
    #gate-err { color: var(--clay); margin-top: 12px; font-weight: 600; }
    #gate-err[hidden] { display: none; }
    .foot { margin-top: 40px; font-size: 13px; color: var(--ink-2); }
  </style>
</head>
<body>
  <div id="gate">
    <div id="gate-box">
      <div class="eyebrow">Draft for review</div>
      <h1>__TITLE__</h1>
      <p>This draft is shared by link and protected by a password. Enter it to read the story.</p>
      <form id="gate-form">
        <input type="password" id="gate-pw" placeholder="Password" autocomplete="current-password" autofocus aria-label="Password" />
        <button type="submit">Open</button>
      </form>
      <p id="gate-err" hidden>That password did not work. Try again.</p>
      <p class="foot">OCHA Centre for Humanitarian Data · <a href="mailto:ocha-datascience@un.org" style="color:inherit">ocha-datascience@un.org</a></p>
    </div>
  </div>
  <script>
    (function () {
      const MAGIC = __MAGIC__;
      const SALT = Uint8Array.from("__SALT__".match(/../g), (h) => parseInt(h, 16));
      const ITERS = __ITERS__;
      const STORE = "page_key_" + location.pathname;
      const DATA = "__DATA__";

      const bytes = Uint8Array.from(atob(DATA), (c) => c.charCodeAt(0));
      if (!MAGIC.every((b, i) => bytes[i] === b)) throw new Error("bad payload");
      const iv = bytes.slice(MAGIC.length, MAGIC.length + 12);
      const body = bytes.slice(MAGIC.length + 12);

      const deriveKey = async (pw) => crypto.subtle.deriveKey(
        { name: "PBKDF2", salt: SALT, iterations: ITERS, hash: "SHA-256" },
        await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]),
        { name: "AES-GCM", length: 256 }, true, ["decrypt"]);

      async function open(key) {
        const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, body);
        const html = new TextDecoder().decode(plain);
        try { localStorage.setItem(STORE, btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey("raw", key))))); } catch (e) {}
        document.open(); document.write(html); document.close();
      }

      async function tryStored() {
        try {
          const b64 = localStorage.getItem(STORE); if (!b64) return false;
          const key = await crypto.subtle.importKey("raw", Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), "AES-GCM", true, ["decrypt"]);
          await open(key); return true;
        } catch (e) { return false; }
      }

      const form = document.getElementById("gate-form"), pw = document.getElementById("gate-pw"),
            err = document.getElementById("gate-err"), btn = form.querySelector("button");
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault(); err.hidden = true; btn.disabled = true; btn.textContent = "Opening…";
        try { await open(await deriveKey(pw.value)); }
        catch (e) { err.hidden = false; btn.disabled = false; btn.textContent = "Open"; pw.select(); }
      });
      tryStored();
    })();
  </script>
</body>
</html>
"""


def encrypt(data: bytes, password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ENC_ITERS).derive(password.encode())
    nonce = os.urandom(12)
    return ENC_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="self-contained HTML document to protect (kept out of git)")
    ap.add_argument("out", type=Path, help="gate page to write, e.g. pages/<slug>/index.html")
    ap.add_argument("--title", required=True, help="title shown on the gate and in the tab")
    ap.add_argument("--password", default=os.environ.get("PAGE_PASSWORD"), help="default: PAGE_PASSWORD env var")
    args = ap.parse_args()
    if not args.password:
        sys.exit("no password: pass --password or set PAGE_PASSWORD")
    src = args.source.read_bytes()
    if not re.search(rb"<!doctype html>", src[:200], re.I):
        sys.exit(f"{args.source} is not a full HTML document (no <!DOCTYPE html>); the gate replaces the whole document")
    salt = secrets.token_bytes(16)
    blob = encrypt(src, args.password, salt)
    shell = (SHELL.replace("__TITLE__", args.title)
                  .replace("__MAGIC__", str(list(ENC_MAGIC)))
                  .replace("__SALT__", salt.hex())
                  .replace("__ITERS__", str(ENC_ITERS))
                  .replace("__DATA__", base64.b64encode(blob).decode()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(shell)
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB; source {len(src) // 1024} KB)")


if __name__ == "__main__":
    main()
