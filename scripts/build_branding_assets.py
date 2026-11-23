from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS_DIR = Path("src/price_tracker/desktop/assets")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

SVGS: dict[str, str] = {
    "logo_dark.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="512" height="512" fill="none">
  <rect x="2" y="2" width="60" height="60" rx="10" fill="#181A1F" stroke="#262930" stroke-width="2"/>
  <line x1="12" y1="35" x2="52" y2="35" stroke="#303540" stroke-width="1.5" stroke-dasharray="3 3"/>
  <path d="M14 18 H27 V28 H38" stroke="#5B82B8" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <path d="M38 28 V42 H47" stroke="#3FA370" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <circle cx="48" cy="42" r="4" fill="#3FA370"/>
</svg>
""",
    "logo_light.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="512" height="512" fill="none">
  <rect x="2" y="2" width="60" height="60" rx="10" fill="#FFFFFF" stroke="#E2E5EB" stroke-width="2"/>
  <line x1="12" y1="35" x2="52" y2="35" stroke="#CBD2DC" stroke-width="1.5" stroke-dasharray="3 3"/>
  <path d="M14 18 H27 V28 H38" stroke="#3B6296" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <path d="M38 28 V42 H47" stroke="#2B8A5B" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <circle cx="48" cy="42" r="4" fill="#2B8A5B"/>
</svg>
""",
    "logo_telegram.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="512" height="512" fill="none">
  <rect width="64" height="64" fill="#181A1F"/>
  <line x1="13" y1="35" x2="51" y2="35" stroke="#303540" stroke-width="1.5" stroke-dasharray="3 3"/>
  <path d="M15 20 H27 V29 H38" stroke="#5B82B8" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <path d="M38 29 V42 H46" stroke="#3FA370" stroke-width="3.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <circle cx="47" cy="42" r="4" fill="#3FA370"/>
</svg>
""",
    "logo_mono_16.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none">
  <path d="M2 4 H6 V8 H10 V12 H13" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <circle cx="13.5" cy="12" r="1.5" fill="currentColor"/>
</svg>
""",
}


def render_svg_to_png_bytes(svg_path: Path, size: int) -> bytes:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"Nieprawidłowy plik SVG: {svg_path}")

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(svg_path: Path, output_ico: Path, sizes: tuple[int, ...] = (16, 32, 48, 256)) -> None:
    png_blobs = [(s, render_svg_to_png_bytes(svg_path, s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(png_blobs))

    offset = 6 + 16 * len(png_blobs)
    entries: list[bytes] = []
    for size, blob in png_blobs:
        dim = 0 if size >= 256 else size
        entries.append(
            struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        )
        offset += len(blob)

    output_ico.write_bytes(header + b"".join(entries) + b"".join(b for _, b in png_blobs))


def main() -> None:
    _app = QGuiApplication(sys.argv)

    for filename, content in SVGS.items():
        (ASSETS_DIR / filename).write_text(content, encoding="utf-8")

    write_ico(ASSETS_DIR / "logo_dark.svg", ASSETS_DIR / "upt_logo.ico")
    (ASSETS_DIR / "upt_alerts_avatar_512.png").write_bytes(
        render_svg_to_png_bytes(ASSETS_DIR / "logo_telegram.svg", 512)
    )
    (ASSETS_DIR / "logo_dark_preview_512.png").write_bytes(
        render_svg_to_png_bytes(ASSETS_DIR / "logo_dark.svg", 512)
    )
    (ASSETS_DIR / "logo_light_preview_512.png").write_bytes(
        render_svg_to_png_bytes(ASSETS_DIR / "logo_light.svg", 512)
    )

    print("Gotowe! Zapisane pliki w:", ASSETS_DIR.resolve())
    for item in sorted(ASSETS_DIR.iterdir()):
        print(" -", item.name)


if __name__ == "__main__":
    main()
