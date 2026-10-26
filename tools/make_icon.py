"""Draw the Shunt mascot — a little bulldozer robot — and emit:

  assets/shunt.png   256x256 in-app logo
  assets/shunt.ico   16..256 exe/window icon

    py tools/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

SS = 4                      # supersample factor
SIZE = 256
OUT = Path(__file__).resolve().parent.parent / "assets"

DARK = "#041007"
BODY = "#26924a"
BODY_HI = "#7dff9f"
CAB = "#35c163"
FACE = "#071208"
EYE = "#c9ffd9"
STEEL = "#9fb4a8"
STEEL_DK = "#6d8579"
TRACK = "#0c1a10"
WHEEL = "#1e3a24"
AMBER = "#ffc14d"


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(v):
        return v * SS

    def box(xy, **kw):
        d.rectangle([s(v) for v in xy], **kw)

    def rbox(xy, r, **kw):
        d.rounded_rectangle([s(v) for v in xy], radius=s(r), **kw)

    def ell(xy, **kw):
        d.ellipse([s(v) for v in xy], **kw)

    def poly(pts, **kw):
        d.polygon([(s(x), s(y)) for x, y in pts], **kw)

    def line(pts, w, **kw):
        d.line([(s(x), s(y)) for x, y in pts], width=s(w), **kw)

    # ---- ground shadow ----
    ell((36, 230, 252, 250), fill=(0, 0, 0, 110))

    # ---- exhaust stack (behind body, rear-left) ----
    rbox((76, 92, 88, 134), 3, fill="#123a1c", outline=DARK, width=s(3))
    ell((74, 88, 90, 100), fill=DARK)

    # ---- tracks ----
    rbox((46, 196, 198, 240), 21, fill=TRACK, outline=DARK, width=s(4))
    for x in (70, 97, 124, 151, 178):
        ell((x - 10, 208, x + 10, 228), fill=WHEEL, outline=DARK, width=s(2))
        ell((x - 3, 215, x + 3, 221), fill=BODY_HI)

    # ---- body ----
    rbox((84, 124, 188, 200), 9, fill=BODY, outline=DARK, width=s(4))
    line([(92, 130), (180, 130)], 3, fill=BODY_HI)
    for y in (152, 160, 168):                      # vents
        line([(96, y), (118, y)], 3, fill="#0c2413")
    ell((164, 138, 174, 148), fill=AMBER, outline=DARK, width=s(2))  # beacon

    # ---- dozer arms ----
    poly([(182, 142), (228, 154), (228, 168), (182, 156)],
         fill=BODY, outline=DARK)
    poly([(182, 174), (228, 186), (228, 200), (182, 190)],
         fill=BODY, outline=DARK)
    line([(190, 150), (224, 160)], 3, fill=BODY_HI)   # hydraulic shine

    # ---- blade (side profile, face leaning forward) ----
    poly([(216, 122), (246, 134), (240, 224), (212, 212)],
         fill=STEEL, outline=DARK)
    poly([(232, 130), (246, 136), (240, 222), (228, 218)],
         fill=STEEL_DK)
    line([(216, 122), (246, 134)], 3, fill="#d5ffe0")
    line([(212, 210), (240, 222)], 4, fill="#33413a")  # cutting edge

    # ---- cab ----
    rbox((100, 66, 174, 130), 11, fill=CAB, outline=DARK, width=s(4))
    line([(108, 72), (166, 72)], 3, fill=BODY_HI)

    # ---- face plate + eyes ----
    rbox((112, 78, 166, 116), 7, fill=FACE, outline=DARK, width=s(2))
    rbox((119, 85, 135, 105), 4, fill=EYE)
    rbox((146, 85, 162, 105), 4, fill=EYE)
    ell((124, 89, 130, 95), fill="#ffffff")          # eye sparkle
    ell((151, 89, 157, 95), fill="#ffffff")

    # ---- antenna + tip ----
    box((158, 44, 163, 68), fill="#123a1c", outline=DARK, width=s(2))
    ell((152, 32, 169, 49), fill=AMBER, outline=DARK, width=s(3))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    img = draw()
    img.save(OUT / "shunt.png")
    img.save(OUT / "shunt.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT / 'shunt.png'} and shunt.ico")


if __name__ == "__main__":
    main()
