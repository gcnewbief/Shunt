"""Draw the NUKE tab icon — radiation trefoil, theme colors.

    py tools/make_nuke_icon.py   ->  assets/nuke.png
"""

from PIL import Image, ImageDraw

S = 256  # draw big, downsample
GREEN = (61, 255, 115, 255)
AMBER = (255, 193, 77, 255)
CLEAR = (0, 0, 0, 0)


def main() -> None:
    img = Image.new("RGBA", (S, S), CLEAR)
    d = ImageDraw.Draw(img)
    c = S // 2
    r_out = int(S * 0.46)
    r_in = int(S * 0.27)
    r_dot = int(S * 0.14)

    # ring
    d.ellipse([c - r_out, c - r_out, c + r_out, c + r_out], fill=GREEN)
    d.ellipse([c - r_in, c - r_in, c + r_in, c + r_in], fill=CLEAR)

    # carve three 60° gaps out of the ring -> three blades
    for a in (30, 150, 270):
        d.pieslice([c - r_out, c - r_out, c + r_out, c + r_out],
                   start=a - 30, end=a + 30, fill=CLEAR)

    # center dot, amber core
    d.ellipse([c - r_dot, c - r_dot, c + r_dot, c + r_dot], fill=GREEN)
    d.ellipse([c - r_dot // 2, c - r_dot // 2,
               c + r_dot // 2, c + r_dot // 2], fill=AMBER)

    img = img.resize((64, 64), Image.LANCZOS)
    img.save("assets/nuke.png")
    print("wrote assets/nuke.png")


if __name__ == "__main__":
    main()
