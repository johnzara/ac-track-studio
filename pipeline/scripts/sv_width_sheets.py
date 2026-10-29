#!/usr/bin/env python3
"""Contact sheets of the top-down road patches with a metre ruler, for reading the asphalt width
by eye where the automatic measure (sv_widths.py) is unsure.  sv_width_sheets.py FROM_M TO_M OUT_PREFIX"""
import json
import sys

from PIL import Image, ImageDraw

from common import ROOT

PXM, ACROSS, ALONG, COLS = 25, 10, 11, 6


def main():
    a, b, out = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    root = ROOT / "streetview" / "ground"
    ps = [p for p in json.loads((root / "index.json").read_text())["patches"] if a <= p["at_m"] <= b]
    if len(sys.argv) > 4 and sys.argv[4] == "--stops":         # only the stops of survey_stops.tsv
        stops = {round(float(l.split("\t")[0])) for l in (ROOT / "survey_stops.tsv").read_text().splitlines()
                 if l.strip() and not l.startswith("#")}
        ps = [p for p in ps if round(p["at_m"]) in stops]
    tw, th = ACROSS * PXM, ALONG * PXM + 30
    for k in range(0, len(ps), COLS * 2):
        grp = ps[k:k + COLS * 2]
        sheet = Image.new("RGB", (tw * COLS, th * ((len(grp) + COLS - 1) // COLS)), (40, 40, 40))
        dr = ImageDraw.Draw(sheet)
        for i, p in enumerate(grp):
            im = Image.open(root / p["file"])
            cx, cy = im.width // 2, im.height // 2
            crop = im.crop((cx - ACROSS * 25, cy - ALONG * 25, cx + ACROSS * 25, cy + ALONG * 25)).resize((tw, ALONG * PXM))
            x0, y0 = (i % COLS) * tw, (i // COLS) * th
            sheet.paste(crop.convert("RGB"), (x0, y0 + 30), crop.split()[3])
            dr.rectangle((x0, y0, x0 + tw - 1, y0 + 29), fill=(0, 0, 0))
            dr.text((x0 + 3, y0 + 1), f"{p['at_m']:.0f} m", fill=(255, 235, 60))
            for mtr in range(ACROSS + 1):
                x = x0 + mtr * PXM
                dr.line((x, y0 + 18, x, y0 + 29), fill=(255, 255, 255))
                if mtr % 2 == 0:
                    dr.text((x + 2, y0 + 12), str(mtr - ACROSS // 2), fill=(200, 200, 200))
            for yy in (0.3, 0.7):                              # the same ruler across the road, twice
                y = y0 + 30 + int(ALONG * PXM * yy)
                for mtr in range(ACROSS + 1):
                    dr.line((x0 + mtr * PXM, y - 3, x0 + mtr * PXM, y + 3), fill=(255, 0, 255))
        sheet.save(f"{out}_{k // (COLS * 2)}.jpg", quality=88)
        print(f"{out}_{k // (COLS * 2)}.jpg", [round(p["at_m"]) for p in grp])


if __name__ == "__main__":
    main()
