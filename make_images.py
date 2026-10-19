"""Generate simple SVG placeholder images for each product in config.json (no real photos)."""
import json, pathlib
cfg = json.load(open(pathlib.Path(__file__).parent / "config.json"))
out = pathlib.Path(__file__).parent / "static" / "img"
out.mkdir(parents=True, exist_ok=True)
for p in cfg["products"]:
    c, a = p["color"], p["accent"]
    if p["shape"] == "circles":
        body = "".join(f'<ellipse cx="{x}" cy="{y}" rx="34" ry="28" fill="{c}" stroke="{a}" stroke-width="4"/>' for x, y in [(100,120),(170,105),(240,125),(135,175),(210,180)])
    elif p["shape"] == "shards":
        body = "".join(f'<polygon points="{pts}" fill="{c}" stroke="{a}" stroke-width="4"/>' for pts in ["70,170 130,90 170,160","150,180 200,80 260,170","110,200 160,150 230,205"])
    elif p["shape"] == "squares":
        body = "".join(f'<rect x="{x}" y="{y}" width="60" height="55" rx="8" fill="{c}" stroke="{a}" stroke-width="4"/>' for x, y in [(80,90),(150,90),(220,90),(115,150),(185,150)])
    else:
        body = f'<rect x="70" y="80" width="200" height="130" rx="12" fill="{c}" stroke="{a}" stroke-width="5"/><rect x="155" y="80" width="30" height="130" fill="#F7D774"/><rect x="70" y="130" width="200" height="30" fill="#F7D774"/>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 260"><rect width="340" height="260" fill="#FFF6EA"/><circle cx="170" cy="145" r="120" fill="#FBE6CC"/>{body}<text x="170" y="245" font-family="Arial" font-size="16" text-anchor="middle" fill="#9a7b5c">SAMPLE IMAGE</text></svg>'''
    (out / f"{p['id']}.svg").write_text(svg)
print("wrote", len(cfg["products"]), "images")
