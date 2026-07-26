#!/usr/bin/env python3
"""Procedural, seamless PBR floor textures for the Marmorikatu model.

Every map is built from FFT-filtered Gaussian noise, so it tiles perfectly in both
axes with no visible seam. Diffuse maps are sRGB, normal maps are tangent-space
OpenGL (+Y up), which is what Blender's Normal Map node expects.

bpy_backend gives floors world-space UVs: u = x_metres * s, v = y_metres * s. So a
texture authored to represent a TILE_M square must be listed in TEXSETS with
scale = 1/TILE_M.  u runs along model x (north->south), v along model y (west->east),
so anything drawn "horizontally" in these images runs north-south in the house.
"""
import numpy as np
from PIL import Image
import os

N = 1024
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tex')

# ---------------------------------------------------------------- noise helpers
def lp(rng, sx, sy, n=N):
    """Seamless smooth noise, unit variance. sx/sy = correlation length in px."""
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.fftfreq(n)[None, :]
    amp = np.exp(-2 * np.pi**2 * ((fx * sx)**2 + (fy * sy)**2))
    out = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(n, n))) * amp))
    return out / (out.std() + 1e-12)

def fbm(rng, sx, sy, octaves=4, gain=0.5, n=N):
    o = np.zeros((n, n)); a = 1.0; tot = 0.0
    for i in range(octaves):
        o += a * lp(rng, sx / 2**i, sy / 2**i, n); tot += a; a *= gain
    o /= tot
    return o / (o.std() + 1e-12)

def roll2(a, dy, dx):
    return np.roll(np.roll(a, dy, axis=0), dx, axis=1)

def per_tile(rng, field, tr, tc, ntr, ntc):
    """Sample `field` with an independent random offset per tile.

    Real tiles are cut from different slabs, so their mottling must NOT flow across a
    grout joint -- a continuous cloud over several tiles reads as a damp stain. The
    offsets are indexed by tile number and the sampling wraps mod N, so the result is
    still exactly periodic over the image and the texture stays seamless.
    """
    oy = rng.integers(0, field.shape[0], size=(ntr, ntc))
    ox = rng.integers(0, field.shape[1], size=(ntr, ntc))
    yy, xx = np.mgrid[0:field.shape[0], 0:field.shape[1]]
    return field[(yy + oy[tr, tc]) % field.shape[0], (xx + ox[tr, tc]) % field.shape[1]]

# ---------------------------------------------------------------- output helpers
def srgb(arr):
    return np.clip(arr, 0, 1)

def save_diff(name, rgb):
    Image.fromarray((srgb(rgb) * 255 + 0.5).astype(np.uint8), 'RGB').save(
        f'{OUT}/{name}_diff.jpg', quality=92, subsampling=0)

NOR_PX = 512      # normals ship at half the diffuse resolution -- see README section 7 (3)

def save_nor(name, height, strength=1.0):
    """Height (arbitrary units, 1.0 ~ 1 px of relief) -> tangent-space normal map.

    Image row 0 is the TOP of the picture but UV v grows upward, so d/dv = -d/drow.

    Written at NOR_PX, not at the authoring resolution. These maps carry only
    low-frequency surface tilt, so half resolution is invisible at any camera distance
    the viewer allows -- but at full resolution they are noisy enough that JPEG barely
    compresses them, and six 1024 px normals cost ~1.9 MB in the GLB on their own. The
    downsample happens after the derivative is taken so the relief keeps its authored
    slope rather than being computed from a blurred height field.
    """
    h = height * strength
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5
    dr = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * 0.5
    nx, ny, nz = -dx, dr, np.ones_like(h)
    L = np.sqrt(nx*nx + ny*ny + nz*nz)
    out = np.stack([nx/L, ny/L, nz/L], -1) * 0.5 + 0.5
    im = Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGB')
    if max(im.size) > NOR_PX:
        im = im.resize((NOR_PX, NOR_PX), Image.LANCZOS)
    im.save(f'{OUT}/{name}_nor.jpg', quality=94, subsampling=0)

def tint(lum, warm, base, dark):
    """Blend between a dark and a base colour by luminance, keeping wood's hue shift."""
    t = np.clip(lum, 0, 1)[..., None]
    c = np.array(dark)/255 + (np.array(base)/255 - np.array(dark)/255) * t
    return c * (1 + warm[..., None] * np.array([0.030, 0.010, -0.020]))

# ================================================================= 1. OAK FLOOR
def make_floor(seed=20260725):
    """Light, warm, matte-lacquered oak plank floor.

    3.0 m square, 15 rows of 200 mm planks, boards 0.8-1.4 m long in an irregular
    stagger. Deliberately low-contrast: no black knots, no grey barnwood cast --
    just quiet cathedral figure and a little board-to-board tone drift.
    """
    TILE_M, PW_M = 3.0, 0.20
    rng = np.random.default_rng(seed)
    NR = int(round(TILE_M / PW_M))                      # 15 plank rows
    edges = [int(round(i * N / NR)) for i in range(NR + 1)]

    figure = fbm(rng, 105, 13, octaves=3)               # cathedral blotches
    grain  = lp(rng, 280, 1.0)                          # fine longitudinal streaks
    pore   = fbm(rng, 5, 1.6, octaves=3)                # open-pore speckle
    dirt   = lp(rng, 260, 190)                          # slow room-scale tone drift

    lum  = np.zeros((N, N))
    warm = np.zeros((N, N))
    hgt  = np.zeros((N, N))
    edge = np.zeros((N, N))                             # 1 at a joint, 0 in the field

    yy = np.arange(N)[:, None] * np.ones((1, N))
    for r in range(NR):
        r0, r1 = edges[r], edges[r + 1]
        rows = slice(r0, r1)
        # --- board joints along this row, wrapped, min 0.78 m apart
        cuts = []
        for _ in range(400):
            if len(cuts) == 3: break
            c = int(rng.integers(0, N))
            if all(min(abs(c - k), N - abs(c - k)) > 266 for k in cuts): cuts.append(c)
        cuts.sort()
        spans = [(cuts[i], cuts[(i + 1) % len(cuts)]) for i in range(len(cuts))]
        for (c0, c1) in spans:
            cols = [slice(c0, c1)] if c1 > c0 else [slice(c0, N), slice(0, c1)]
            dy, dx = int(rng.integers(0, N)), int(rng.integers(0, N))
            fg = roll2(figure, dy, dx); gr = roll2(grain, dy, dx); po = roll2(pore, dy, dx)
            tone = rng.normal(0, 0.036)                 # board-to-board lightness
            wtone = rng.normal(0, 0.60)                 # board-to-board warmth
            rings = 4.6 + rng.normal(0, 0.5)
            for cs in cols:
                t = (yy[rows, cs] - r0) / (r1 - r0)     # 0..1 across the plank
                d = t * rings + 1.05 * fg[rows, cs] + 0.22 * gr[rows, cs]
                ring = 0.5 - 0.5 * np.cos(2 * np.pi * d)
                ring = ring ** 2.4                      # thin dark lines, wide light field
                v = (0.735 + tone
                     - 0.125 * ring
                     + 0.055 * gr[rows, cs]
                     + 0.030 * fg[rows, cs]
                     - 0.035 * np.clip(po[rows, cs], 0, None))
                lum[rows, cs] = v
                warm[rows, cs] = wtone + 0.45 * fg[rows, cs] - 0.5 * ring
                hgt[rows, cs] = -0.22 * ring - 0.10 * np.clip(po[rows, cs], 0, None)
        # --- long edges of the row: 2 px micro-bevel + 1 px joint line
        for e in (r0, r1 % N):
            for k, f in ((0, 1.0), (1, 0.55), (-1, 0.55), (2, 0.22), (-2, 0.22)):
                edge[(e + k) % N, :] = np.maximum(edge[(e + k) % N, :], f)
        # --- end joints
        for c in cuts:
            for k, f in ((0, 1.0), (1, 0.5), (-1, 0.5)):
                edge[rows, (c + k) % N] = np.maximum(edge[rows, (c + k) % N], f)

    lum = lum + 0.020 * dirt - 0.115 * edge
    rgb = tint(lum, warm, base=(214, 188, 154), dark=(120, 92, 64))
    # a lacquered floor never goes fully matte-dark; lift the shadows a touch
    rgb = rgb * 0.94 + 0.06
    save_diff('floor', rgb)
    save_nor('floor', hgt - 1.35 * edge, strength=0.85)
    return rgb

# ================================================================= 2. LIGHT TILE
def make_tile(seed=31415):
    """Matte porcelain, 300 x 600 mm, running bond, warm light grey.

    Authored for a 2.4 m square = 4 x 8 whole tiles, so the module divides the 1024 px
    canvas exactly (256 / 128) and the joint lines meet across the texture repeat. A
    3.0 m square would need 5 x 10 tiles of 204.8 px and leave a 4 px sliver at the
    border, which shows up as a broken joint every repeat.
    """
    rng = np.random.default_rng(seed)
    NTX, NTY = 4, 8
    LX, LY = N // NTX, N // NTY                         # 600 mm along u, 300 mm along v
    GR = 2                                              # grout half-width in px

    yy, xx = np.mgrid[0:N, 0:N]
    row = yy // LY
    shift = (row % 2) * (LX // 2)                       # running bond
    u = (xx + shift) % LX
    v = yy % LY
    tr, tc = row, ((xx + shift) // LX) % NTX            # %NTX: the two half tiles that
    grout = ((u < GR) | (u >= LX - GR) |                # straddle the wrap are one tile
             (v < GR) | (v >= LY - GR)).astype(float)
    grout = np.maximum(grout, 0.45 * (((u < GR + 2) | (u >= LX - GR - 2) |
                                       (v < GR + 2) | (v >= LY - GR - 2)).astype(float)))

    tone = rng.normal(0, 0.009, size=(NTY, NTX))[tr, tc]        # slab-to-slab lightness
    body = per_tile(rng, fbm(rng, 30, 30, octaves=4), tr, tc, NTY, NTX)
    drift = lp(rng, 320, 320)                                   # very slow room-scale tone
    speck = lp(rng, 1.3, 1.3)
    lum = 0.845 + tone + 0.022 * body + 0.010 * drift + 0.010 * speck
    lum = lum * (1 - grout) + (0.640 + 0.010 * drift) * grout

    # keep the per-tile hue shift small: porcelain from one batch varies in lightness,
    # not in colour, and a strong coupling here reads as a cream/blue patchwork
    warm = 0.25 * body + tone / 0.009 * 0.15
    rgb = tint(lum, warm, base=(233, 231, 226), dark=(96, 95, 92))
    save_diff('tile', rgb)
    save_nor('tile', -1.5 * grout + 0.08 * body, strength=1.0)
    return rgb

# ================================================================= 3. DARK TILE
def make_tiledark(seed=2718):
    """Anthracite 300 x 300 mm stone-look porcelain for the sauna / wet rooms.

    2.4 m square = 8 x 8 whole tiles of 128 px, so the module divides the canvas
    exactly. Deliberately quiet: no marbling veins and no bright spatter, just a
    slow within-tile cloud and a fine sand grain, which is what matte stone-look
    porcelain actually reads as underfoot.
    """
    rng = np.random.default_rng(seed)
    NT = 8
    L = N // NT                                          # 300 mm both ways
    GR = 2
    yy, xx = np.mgrid[0:N, 0:N]
    u, v = xx % L, yy % L
    tr, tc = yy // L, xx // L
    grout = ((u < GR) | (u >= L - GR) | (v < GR) | (v >= L - GR)).astype(float)
    grout = np.maximum(grout, 0.4 * (((u < GR + 2) | (u >= L - GR - 2) |
                                      (v < GR + 2) | (v >= L - GR - 2)).astype(float)))

    tone = rng.normal(0, 0.011, size=(NT, NT))[tr, tc]
    body = per_tile(rng, fbm(rng, 22, 22, octaves=4), tr, tc, NT, NT)
    grain = fbm(rng, 2.6, 2.6, octaves=3)
    lum = 0.350 + tone + 0.026 * body + 0.020 * grain
    lum = lum * (1 - grout) + (0.250 + 0.010 * body) * grout
    rgb = tint(lum, 0.3 * body, base=(205, 208, 209), dark=(46, 48, 50))
    save_diff('tiledark', rgb)
    save_nor('tiledark', -1.5 * grout + 0.07 * body + 0.04 * grain, strength=1.0)
    return rgb

# ================================================================= 4. BASEMENT FLOOR
def _concrete(name, seed, lum0, base, dark, pit_amp, warm_amp=0.25):
    """Sealed concrete slab, authored for a 4.0 m square so a 10 x 7 m room barely repeats."""
    rng = np.random.default_rng(seed)
    broad = 0.6 * lp(rng, 150, 150) + 0.4 * lp(rng, 300, 300)
    trowel = lp(rng, 60, 14)                              # directional float marks
    fine = fbm(rng, 9, 9, octaves=4)
    speck = lp(rng, 1.2, 1.2)
    pits = np.clip(fbm(rng, 3.2, 3.2, octaves=3) - 1.6, 0, None)

    lum = (lum0 + 0.065 * broad + 0.028 * trowel + 0.030 * fine
           + 0.014 * speck - pit_amp * pits)
    rgb = tint(lum, warm_amp * broad, base=base, dark=dark)
    save_diff(name, rgb)
    save_nor(name, 0.35 * fine + 0.20 * trowel - 2.2 * pits, strength=0.9)
    return rgb

def make_cdark(seed=161803):
    """Dark sealed / painted concrete -- the kellari rec room (VAR1)."""
    return _concrete('cdark', seed, 0.300, (176, 170, 163), (30, 29, 28), 0.085)

def make_cfloor(seed=577215):
    """Pale untreated concrete -- kellari VAR2, tekninen tila, carport, terrace steps.

    Replaces the old photo-scanned concrete map on FLOORS: that one is not perfectly
    seamless, and at floor scale its repeat showed up as regular straight lines across
    the big basement store. Walls (ConcreteW) still use the photo map, where the
    vertical uv layout hides the join.
    """
    return _concrete('cfloor', seed, 0.560, (215, 211, 204), (120, 117, 112), 0.055,
                     warm_amp=0.18)

# ================================================================= 5. VERTICAL BOARD
def make_vboard(seed=104729):
    """White-painted vertical board cladding -- the panels between the windows.

    Same timber cladding as the rest of the facade, only turned on end and painted
    white (owner's photo). Authored for a 1.92 m square: 16 boards of 120 mm, so the
    board module is exactly 64 px and the map stays seamless in both axes.

    Listed in TEXSETS with the 'wall' uv (u = x+y, v = z), so image COLUMNS run
    horizontally along the facade and image ROWS run up it -- the stripes below are
    drawn across the columns, which makes the boards stand vertically on the wall.
    Nothing varies along v, so the 1.92 m vertical repeat is invisible.
    """
    TILE_M, BW_M = 1.92, 0.12
    NB = int(round(TILE_M / BW_M))                      # 16 boards
    W = N // NB                                         # 64 px per board
    rng = np.random.default_rng(seed)

    col = np.arange(N)[None, :]                         # u -> image columns
    t = (col % W) / W                                   # 0..1 across one board
    edge = np.minimum(t, 1 - t) * W                     # px to the nearest joint
    seam = np.exp(-(edge / 2.1)**2)                     # tight shadow line
    board = rng.normal(size=(1, NB)).repeat(W, axis=1)  # per-board tone drift

    grain = fbm(rng, 1.7, 90, octaves=4, gain=0.55)     # figure runs up the board
    brush = lp(rng, 2.4, 260)                           # roller streaks, same way
    broad = lp(rng, 130, 260)                           # slow weathering drift
    dirt = np.clip(fbm(rng, 26, 70, octaves=3) - 0.9, 0, None)   # faint grime runs

    lum = (0.855 + 0.026 * grain + 0.020 * brush + 0.034 * broad
           + 0.013 * board - 0.030 * dirt - 0.620 * seam)
    rgb = tint(lum, 0.30 * broad, base=(250, 249, 245), dark=(96, 97, 98))
    save_diff('vboard', rgb)

    cup = 0.55 * np.sin(np.pi * t)                      # boards very slightly crowned
    proud = 0.30 * board                                # a few sit a shaving forward
    save_nor('vboard', -3.2 * seam + cup + proud + 0.20 * grain + 0.10 * brush,
             strength=1.0)
    return rgb

# ================================================================= contact sheet
def sheet(pairs, path):
    cell = 300
    im = Image.new('RGB', (cell * len(pairs), cell + 22), 'white')
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    for i, (nm, arr) in enumerate(pairs):
        t = Image.fromarray((srgb(arr) * 255).astype(np.uint8), 'RGB')
        # 2x2 so any seam shows up
        q = t.resize((cell // 2, cell // 2), Image.LANCZOS)
        for a in range(2):
            for b in range(2):
                im.paste(q, (i * cell + b * cell // 2, 22 + a * cell // 2))
        d.text((i * cell + 6, 5), nm, fill='black')
    im.save(path)

if __name__ == '__main__':
    out = [('oak floor 3.0 m', make_floor()),
           ('tile 2.4 m', make_tile()),
           ('tiledark 2.4 m', make_tiledark()),
           ('cdark 4.0 m', make_cdark()),
           ('cfloor 4.0 m', make_cfloor()),
           ('vboard 1.92 m', make_vboard())]
    sheet(out, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tex_sheet.png'))
    for f in sorted(os.listdir(OUT)):
        print(f'  {f:24s} {os.path.getsize(OUT+"/"+f)/1024:7.1f} KB')
