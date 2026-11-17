"""Generate NPL application, favicon and connector sizes from the approved mark."""
from pathlib import Path
import shutil
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
SOURCE = ROOT / "web" / "brand" / "npl-app.png"
SIZES = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]


def main():
    if not SOURCE.is_file():
        raise SystemExit("Render NPL SVG masters first: node tools/render_brand.mjs")
    OUT.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as source:
        image = source.convert("RGBA")
        image.save(OUT / "npl.png")
        image.save(OUT / "npl.ico", sizes=SIZES)
        for size in (16, 32, 48, 128):
            target = ROOT / "extension" / "icons" / f"icon{size}.png"
            image.resize((size, size), Image.Resampling.LANCZOS).save(target)
    shutil.copyfile(OUT / "npl.ico", ROOT / "web" / "brand" / "npl.ico")
    # Legacy developer/shortcut references also show the new image.
    shutil.copyfile(OUT / "npl.png", OUT / "paperwhale.png")
    shutil.copyfile(OUT / "npl.ico", OUT / "paperwhale.ico")
    print("Generated NPL application icon, favicon and connector sizes")


if __name__ == "__main__":
    main()
