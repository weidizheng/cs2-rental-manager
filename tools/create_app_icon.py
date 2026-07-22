"""Generate the Windows icon used by the portable CS2 rental manager build."""

from pathlib import Path

from PIL import Image, ImageDraw


OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "cs2-rental-manager.ico"


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((12, 12, 244, 244), radius=52, fill="#111827")
    draw.rounded_rectangle((20, 20, 236, 236), radius=44, outline="#60a5fa", width=8)
    # A small market chart + rental tag: readable even at 32 px.
    draw.line((54, 166, 101, 122, 139, 143, 202, 75), fill="#7dd3fc", width=15, joint="curve")
    draw.ellipse((191, 64, 213, 86), fill="#a7f3d0")
    draw.rounded_rectangle((52, 178, 204, 204), radius=12, fill="#334155")
    draw.rectangle((73, 184, 92, 198), fill="#a7f3d0")
    draw.rectangle((107, 184, 126, 198), fill="#fcd34d")
    draw.rectangle((141, 184, 160, 198), fill="#f9a8d4")
    canvas.save(OUTPUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUTPUT)


if __name__ == "__main__":
    main()
