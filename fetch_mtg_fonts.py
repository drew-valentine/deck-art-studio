#!/usr/bin/env python3
"""
Download official MTG card fonts (Beleren + MPlantin) from GitHub.

These are the actual fonts used on Magic: The Gathering cards:
- Beleren Bold — card names
- Beleren — type lines
- MPlantin — rules text
- MPlantin Italic — flavor text

Run once: python fetch_mtg_fonts.py
"""

import urllib.request
import urllib.error
from pathlib import Path

FONTS_DIR = Path(__file__).parent / "fonts"

FONT_URLS = {
    "Beleren2016-Bold.ttf": "https://raw.githubusercontent.com/Saeris/typeface-beleren-bold/master/Beleren2016-Bold.ttf",
    "Beleren-Bold.ttf": "https://raw.githubusercontent.com/magarena/magarena/master/resources/cardbuilder/fonts/Beleren-Bold.ttf",
    "MPlantin.ttf": "https://raw.githubusercontent.com/AlexandreArpin/mtg-font/master/fonts/Mplantin.ttf",
    "MPlantin-Italic.ttf": "https://raw.githubusercontent.com/AlexandreArpin/mtg-font/master/fonts/Mplantin-Italic.ttf",
}

HEADERS = {
    "User-Agent": "MTGProxyDeckBuilder/1.0",
    "Accept": "*/*",
}


def download_fonts(force: bool = False) -> dict:
    """Download MTG fonts. Returns dict with results."""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {"downloaded": [], "cached": [], "failed": []}

    for filename, url in FONT_URLS.items():
        dest = FONTS_DIR / filename

        if dest.exists() and dest.stat().st_size > 1000 and not force:
            results["cached"].append(filename)
            continue

        print(f"  Downloading {filename}...")
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            dest.write_bytes(data)
            results["downloaded"].append(filename)
            print(f"    ✓ {len(data):,} bytes")
        except Exception as e:
            results["failed"].append(filename)
            print(f"    ✗ {e}")

    return results


def install_fonts():
    """Install downloaded fonts into system font directory for cairosvg/SVG rendering.

    Handles macOS (~/Library/Fonts/) and Linux (~/.local/share/fonts/).
    """
    import shutil
    import subprocess
    import platform

    # Determine correct font directory for this OS
    if platform.system() == "Darwin":  # macOS
        user_fonts = Path.home() / "Library" / "Fonts"
    else:  # Linux
        user_fonts = Path.home() / ".local" / "share" / "fonts"

    user_fonts.mkdir(parents=True, exist_ok=True)

    installed = 0
    for ttf in FONTS_DIR.glob("*.ttf"):
        dest = user_fonts / ttf.name
        if not dest.exists() or dest.stat().st_size != ttf.stat().st_size:
            shutil.copy2(ttf, dest)
            installed += 1
            print(f"  Installed: {ttf.name} -> {dest}")

    if installed:
        # Rebuild font cache (Linux uses fc-cache; macOS picks up ~/Library/Fonts automatically)
        if platform.system() != "Darwin":
            try:
                subprocess.run(["fc-cache", "-f"], capture_output=True, timeout=10)
                print(f"  Font cache rebuilt ({installed} fonts installed)")
            except Exception:
                print(f"  Fonts copied ({installed}), run 'fc-cache -f' manually if needed")
        else:
            print(f"  {installed} font(s) installed to ~/Library/Fonts/ (available immediately)")
    else:
        print("  All fonts already installed")


def fonts_available() -> bool:
    """Check if MTG fonts are downloaded AND installed."""
    import platform

    if not FONTS_DIR.exists():
        return False
    if not all((FONTS_DIR / f).exists() for f in ["Beleren2016-Bold.ttf", "MPlantin.ttf"]):
        return False

    # Also check they're installed in the system font directory
    if platform.system() == "Darwin":
        font_dir = Path.home() / "Library" / "Fonts"
    else:
        font_dir = Path.home() / ".local" / "share" / "fonts"

    return all((font_dir / f).exists() for f in ["Beleren2016-Bold.ttf", "MPlantin.ttf"])


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv

    print("Downloading MTG card fonts...")
    results = download_fonts(force=force)
    print(f"\nDownloaded: {len(results['downloaded'])}, "
          f"Cached: {len(results['cached'])}, "
          f"Failed: {len(results['failed'])}")

    if results["downloaded"] or not fonts_available():
        print("\nInstalling fonts for system rendering...")
        install_fonts()

    print("\nDone!")
