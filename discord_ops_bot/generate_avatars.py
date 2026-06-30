#!/usr/bin/env python3
"""
generate_avatars.py — Generate 512x512 PNG avatars for the 5 ops agents.

Draws at 4x (2048px) and downsamples with LANCZOS for clean anti-aliased
edges. Output -> assets/avatars/<agent>.png
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "assets" / "avatars"
OUT.mkdir(parents=True, exist_ok=True)

S = 2048   # supersample size
F = 512    # final size
W = S // 32  # base stroke width unit

COLORS = {
    "ops":      "#5B6B7C",  # slate grey
    "ci":       "#2ECC71",  # green
    "review":   "#3498DB",  # blue
    "deploy":   "#E67E22",  # orange
    "research": "#9B59B6",  # purple
}
WHITE = "#FFFFFF"


def canvas(color):
    img = Image.new("RGB", (S, S), color)
    return img, ImageDraw.Draw(img)


def save(img, name):
    img = img.resize((F, F), Image.LANCZOS)
    path = OUT / f"{name}.png"
    img.save(path)
    print(f"  wrote {path}")


def gear(cx, cy, r_outer, r_inner, r_hole, teeth, draw, color):
    import math
    n = teeth
    pts = []
    for i in range(n * 2):
        ang = math.pi * i / n
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts, fill=color)
    draw.ellipse((cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole), fill=COLORS["ops"])


# ── Ops Agent: gear ─────────────────────────────────────────────────────────
img, d = canvas(COLORS["ops"])
cx, cy = S // 2, S // 2
gear(cx, cy, r_outer=S * 0.40, r_inner=S * 0.315, r_hole=S * 0.16, teeth=8, draw=d, color=WHITE)
save(img, "ops")

# ── CI Agent: checkmark ──────────────────────────────────────────────────────
img, d = canvas(COLORS["ci"])
lw = int(S * 0.10)
d.line([(S*0.22, S*0.54), (S*0.42, S*0.74), (S*0.80, S*0.28)], fill=WHITE, width=lw, joint="curve")
# round the line caps
for x, y in [(S*0.22, S*0.54), (S*0.42, S*0.74), (S*0.80, S*0.28)]:
    d.ellipse((x-lw/2, y-lw/2, x+lw/2, y+lw/2), fill=WHITE)
save(img, "ci")

# ── Review Agent: magnifying glass ──────────────────────────────────────────
img, d = canvas(COLORS["review"])
lens_cx, lens_cy, lens_r = S*0.42, S*0.42, S*0.24
ring = int(S * 0.075)
d.ellipse((lens_cx-lens_r, lens_cy-lens_r, lens_cx+lens_r, lens_cy+lens_r), outline=WHITE, width=ring)
# handle
import math
angle = math.radians(45)
hx1 = lens_cx + (lens_r - ring*0.2) * math.cos(angle)
hy1 = lens_cy + (lens_r - ring*0.2) * math.sin(angle)
hx2 = hx1 + S*0.26 * math.cos(angle)
hy2 = hy1 + S*0.26 * math.sin(angle)
handle_w = int(S * 0.09)
d.line([(hx1, hy1), (hx2, hy2)], fill=WHITE, width=handle_w)
d.ellipse((hx2-handle_w/2, hy2-handle_w/2, hx2+handle_w/2, hy2+handle_w/2), fill=WHITE)
save(img, "review")

# ── Deploy Agent: rocket (upward arrow + body) ──────────────────────────────
img, d = canvas(COLORS["deploy"])
cx = S * 0.5
# body (rounded rectangle via ellipse + rectangle)
body_w, body_top, body_bot = S*0.16, S*0.32, S*0.62
d.rounded_rectangle((cx-body_w, body_top, cx+body_w, body_bot), radius=int(S*0.10), fill=WHITE)
# nose cone
d.polygon([(cx-body_w, body_top+S*0.02), (cx+body_w, body_top+S*0.02), (cx, S*0.16)], fill=WHITE)
# fins
fin_w = S*0.12
d.polygon([(cx-body_w, body_bot-S*0.05), (cx-body_w-fin_w, body_bot+S*0.10), (cx-body_w, body_bot+S*0.02)], fill=WHITE)
d.polygon([(cx+body_w, body_bot-S*0.05), (cx+body_w+fin_w, body_bot+S*0.10), (cx+body_w, body_bot+S*0.02)], fill=WHITE)
# window
win_r = S*0.045
d.ellipse((cx-win_r, body_top+S*0.10-win_r, cx+win_r, body_top+S*0.10+win_r), fill=COLORS["deploy"])
# exhaust flame
d.polygon([(cx-body_w*0.6, body_bot+S*0.02), (cx+body_w*0.6, body_bot+S*0.02), (cx, body_bot+S*0.18)], fill=WHITE)
save(img, "deploy")

# ── Research Agent: bar chart ───────────────────────────────────────────────
img, d = canvas(COLORS["research"])
base_y = S * 0.72
bar_w = S * 0.13
gap = S * 0.06
heights = [S*0.22, S*0.38, S*0.30, S*0.50]
total_w = len(heights) * bar_w + (len(heights)-1) * gap
x0 = (S - total_w) / 2
for i, h in enumerate(heights):
    x = x0 + i * (bar_w + gap)
    d.rounded_rectangle((x, base_y - h, x + bar_w, base_y), radius=int(S*0.02), fill=WHITE)
# baseline
d.rounded_rectangle((S*0.12, base_y, S*0.88, base_y + S*0.025), radius=int(S*0.01), fill=WHITE)
save(img, "research")

print("Done.")
