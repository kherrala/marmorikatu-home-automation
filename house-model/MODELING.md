# Modeling reference — Marmorikatu 10 house model

The maintainer's guide to **changing** the model. Nothing here is needed to *consume*
it — that is [`README.md`](README.md), the contract the mobile app relies on. The
render/light rig for the native apps is [`LIGHTING.md`](LIGHTING.md).

Everything builds locally: no cloud step, no session state, no network (three.js is
vendored). Source of truth is `spec.py`; `bpy_backend.py` turns its builder calls into
Blender meshes.

---

## 1. Sources

The geometry was derived from the architect/electrical/LVI drawings, which live **outside
this repo** in a sibling `marmorikatu-dokumentit/` folder (not tracked — they are the
owner's permit set). The sheets that matter:

| sheet | gives |
|---|---|
| `1krs_pohja50_1.pdf` (richer variant), `2krs_pohja50.pdf`, `0krs_pohja50.pdf` | plans → x/y |
| `julkisivut.pdf` | all four elevations → z, roof, window heads/sills, cladding |
| `Asemapiirustus 3.9.2009.pdf` | site plan → ground levels, plot, driveway, carport |
| `Sähkö` set | light positions (`build_lights`) |
| `LVI` set | floor-heating circuits (`HEAT`) |

The DWG vector extraction that first seeded `spec.py` is superseded — the PDFs are what
everything is checked against now. **The owner outranks the drawings:** the 2009 permit
set predates some as-built features (pergola, sunroof, north terrace screen, back garden),
so "not on the sheet" is a question to ask, never a licence to delete. Overrides are noted
inline in `spec.py`.

## 2. Build loop

Edit `spec.py` → rebuild in Blender → pack → look at it.

In Blender's Python console (one line — globals do not survive between calls, so the
backend is re-exec'd into a fresh namespace each time):

```python
ns={}; BASE='/ABS/PATH/TO/house-model'   # absolute path to this folder
exec(compile(open(BASE+'/bpy_backend.py').read(),'b','exec'),ns); ns['hk_run'](BASE); ns['hk_export'](BASE)
```

Then, from this folder in a shell:

```sh
python3 pack.py          # needs numpy + Pillow; reads the GLB, no Blender needed
```

`hk_run` builds the scene and returns its object count; `hk_export` writes
`marmorikatu.blend`, `marmorikatu-house.glb` and `marmorikatu-house.usdz`. `pack.py`
writes `out/marmorikatu-3d.html` + `out/cameras.json` and copies both up to the tracked
root, and prints the anchor tally (`N rooms, N lights, N heating circuits`).

**Requirements:** Blender 4.2+ (its bundled Python has `usd-core`, which the USDZ
post-process needs) and a system Python 3.10+ with `numpy` + `Pillow`.

**Object-count tripwire.** Note the count `hk_run` prints and watch it across edits — a
number that moved when you did not mean it to is the fastest sign an edit did more than you
thought. Do not hardcode the value in docs: it drifts every time the spec grows (it has
already gone stale twice), so the *last build's* number is the only meaningful baseline.

## 3. Blender quirks that bite

- **Globals do not persist** between console calls / `execute_blender_code` invocations —
  every snippet must re-exec `bpy_backend.py` into a fresh namespace (hence the one-liner).
- **Image cache.** Blender keys textures by filepath, so after regenerating anything in
  `tex/` a rebuilt scene keeps the old bitmap. Force a reload in the same call:
  ```python
  import bpy
  for im in bpy.data.images: im.reload()
  ```
- **Engine name.** This Blender reports EEVEE as `BLENDER_EEVEE`, not `BLENDER_EEVEE_NEXT`
  — scripts setting `scene.render.engine` need the former.
- **`hk_export` is very loud** (~130 KB of USD/glTF progress per run). If you drive it over
  a tool bridge with an output-size limit, wrap it in `contextlib.redirect_stdout` and
  print only the counts/sizes you want back.

## 4. Builder API

`spec.py` never touches `bpy`; it calls methods on a builder object, which is why the
checking scripts (§6) can replay the whole spec in a plain shell against a recording stub.
The methods on `BlenderB` (`bpy_backend.py`):

```
polyseg(name, segs, w, z0, z1, mat)          extruded polyline band
tube(name, pts, r, z, mat, sides=8)          round tube swept in a HORIZONTAL plane (heating pipes)
tube3(name, pts, r, mat, sides=10)           round tube along a full 3-D polyline (raking runs:
                                             downpipe swan-necks, gutter shoes) — parallel-transport framed
box(name, xs, ys, zs, mat)                   xs/ys/zs are (min, max) pairs
cyl(name, x, y, z0, z1, r, mat, segs=20)
sph(name, x, y, z, r, mat)
slab(name, poly, z0, z1, mat, holes=None)
room(name, poly, mat, z=0.0, holes=None)     the Room_ floor patches
roofquad(name, pts, thick, mat)              pts are the TOP surface; extrudes DOWNWARD
prism(name, x0, x1, poly_yz, mat, axis='x')  axis='x' → profile in (y,z); 'y' → (x,z)
```

Two traps: `roofquad` grows the slab *downward* from the top surface, so a deck described
by its underside sits one thickness too high; `prism` builds a single n-gon, so a concave
profile comes out wrong — split it into convex pieces. Use `tube` for level runs (it sweeps
in the horizontal plane) and `tube3` for anything that changes height along its length.

Above these sit the spec's own helpers: `W`/`wall_x`/`wall_y` for walls with openings,
`face` for window/door frames, the furniture shorthands (`bed`, `table`, `chair`,
`wardrobe`, `rug`, `plant`, `sofa`, `toilet`). Top-level builders, run in order by
`build_all`: `build_kellari`, `build_krs1`, `build_krs2`, `build_roof`, `build_katos`,
`build_lights`, `build_heat`. The heating autorouter lives in `build_heat` (`_area` down):
`_lanes` lays serpentine lanes in a room polygon, `_field_path` walks them, `_link` joins
fields, `_fillet` rounds corners; `HEAT` holds per-circuit metadata and `HEATAXIS`
overrides lane direction for four circuits.

Key `spec.py` constants: wall thicknesses `EXT=0.30`, `KXT=0.34` (kellari concrete+render),
`INT=0.10`; storey heights `H_K=2.54`, `H_1=2.56`, `H_1E=3.01`, `H_L=2.60`, `H_2=2.58`,
`H_2E=3.19`; floor datums `Z_K=−3.04`, `Z_2=3.01`; outline polygons `P1`, `PK`, `P2`.

Anchors the app depends on (counted live by `pack.py`; current spec): **25 `Room_`
patches, 41 `Light_` anchors, 19 `Heat_` circuits, 5 manifolds**. Anchors are the dot-free
names; sub-parts carry a `.` suffix and are excluded from `cameras.json`.

## 5. Reading the drawings — calibrations

The plan PDFs report `chars = 0` (dimension strings are outlined vector art, not text), so
numbers are read off geometry, not extracted. Decompose `page.curves` as well as
`page.lines` or a sweep comes back sparse (the 1krs sheet goes 2 281 → 56 453 segments).

**Datum.** `1. krs +135.90 = model z 0`, so `model z = absolute − 135.90` (not +136.00).
Cross-checked off the site plan's own levels (KELL +132.86 → −3.04, KATOS +135.35 → −0.55).

**Elevations (`julkisivut.pdf`).** Scale `S = 28.3465` pt/m. For a PDF point `(ptx, pty)`:

| elevation | horizontal | vertical |
|---|---|---|
| LÄNSI (west) | `x = (ptx − 177.2)/S` | `z = (289.80 − pty)/S − 0.08` |
| ETELÄ (south) | `y = (ptx − 882.81)/S` | `z = (289.80 − pty)/S − 0.08` |
| ITÄ (east) | `x = (547.5 − ptx)/S` | `z = (289.80 − pty)/S − 0.08` |
| POHJOINEN (north) | `y = 36.651 − ptx/S` | `z = (686.95 − pty)/S − 0.08` |

The `−0.08` is the sheet's datum offset. **`pty` here is TOP-DOWN**; `pdfplumber` line
objects give bottom-up `y0`/`y1`, so flip them (`pty = page.height − y0`) first — feed
bottom-up values straight in and every z comes out mirrored about the datum, silently and
plausibly. (`curve['pts']` has the opposite convention — top-down while `lines` are
bottom-up.)

**Plans (`1krs_pohja50_1.pdf`).** `S = 56.6795` pt/m; `plan_x = (ptx − 142.283)/S`,
`plan_y = (646.62 − pty)/S`, with **`pty` taken straight off `line.y0` (bottom-up — no flip
here, unlike the elevations above)**. Plan is authoritative for x/y, elevation for z. There is
a **systematic offset: elevation x = plan x + 0.016** — subtract 16 mm from any x taken off
`julkisivut.pdf`.

> The y datum was recorded here as `795.79`, which is **wrong by 149.2 pt = 2.63 m**.
> Re-solved against `F1.kit_et`, the kitchen wall at x 7.92 running y 0.77..4.07: the sheet
> draws it at `ptx` 7.874..7.968, `pty` 415.9..603.0 — 3.301 m against the model's 3.300 — and
> both ends independently give `D = 646.64` / `646.59`, agreeing to 0.9 mm. Cross-checked on
> `F1.tk_n` (y 2.50 → 2.550), `F1.mh_n` (3.90 → 3.944) and `F1.wS.blk` (0.00 → 0.014).

Two things to restrict before searching this sheet:

- It carries **several drawings**, not only the 1. krs plan — stacked 6.27 m runs at plan-x
  21.5..27.8 and 9.25 m runs at 40.4..49.7 belong to other views. Clip to plan-x −0.1..17.1.
- The three full-width 17.14 m lines near `pty` 64 / 86 / 108 are the **dimension stack**, not
  walls. They sit 0.39 m apart, which is dimension-line spacing at 1:50 — no wall is that
  thick. This is the trap that made the y axis look unsolvable the first time.

**Site plan (`Asemapiirustus 3.9.2009.pdf`).** 1:200, and the drawing is rotated ~165° on
the sheet. `S = 14.17064` pt/m, `th = 165.171°`:
```
mx = -0.0682181*ptx + 0.0180610*pty -   1.7721
my = -0.0180610*ptx - 0.0682181*pty + 139.5134
```
Fitted on the seven heavy footprint edges (rms 1.9 mm). No fill/hatch anywhere — tell
surfaces apart by linewidth (1.44 walls, 0.96 roof, 0.72 site linework, 0.48 plants/text).
Two traps that produce plausible garbage: `pdfplumber` lines are bbox-normalised so
segment direction sign is lost (classify by |angle| and reconstruct endpoints), and
`curve['pts']` is top-down while lines are bottom-up.

**Cladding convention:** louver/cladding boards are drawn **three lines per board** (face +
two joint/shadow lines) summing to the 0.100 m module — group in threes and measure
group-top to group-top, or you get the wrong pitch. Hidden geometry is clipped: verticals
behind a rail bottom out at the rail line, not a real sill.

The plan/elevation extractors are one-off scripts (the exact analogue of each other,
same both-endpoints-inside-the-band and merge/sort logic); they are **not kept in the
repo** — rewrite from the formulas above when needed.

**Overlay check (the reliable verification).** Render the PDF page with PyMuPDF at a known
px/m, composite the model render over it, and recolour the drawing's ink so the two are
distinguishable — misalignment is obvious in a way comparing two numbers never is:
```python
import fitz
pix = fitz.open(pdf)[0].get_pixmap(matrix=fitz.Matrix(100/28.3465, 100/28.3465))  # 100 px/m
ink = pdf_gray < 170
out[ink] = out[ink]*0.15 + np.array([255,40,40])*0.85    # red = drawing, everything else = model
```
For "is this surface covered / does this overlap", prefer a `scene.ray_cast` grid reading
back `obj.data.materials[0].name` and an **exact ORTHO plan render** at a known px/m over a
perspective view — a perspective is for judging whether something *looks* right, never for
deciding whether it *is*.

## 6. Checking scripts

Both replay `spec.build_all` against a recording stub — no `bpy`, so they run in a plain
shell in about a second.

```sh
python3 check_dupes.py [Material]     # duplicate geometry: same-material boxes overlapping >50%
python3 zfight.py [min_area_m2]       # coplanar same-facing face pairs vs zfight-baseline.txt
```

`check_dupes.py` catches the classic defect — two passes adding the same panel, whose
boxes never coincide exactly so an exact-extent test misses them. Known-benign hits are
perpendicular outside-corner wall intersections and the downpipe `.clip`↔`.fall`
overlaps; anything else is a real duplicate. `zfight.py` diffs against a committed baseline
and exits non-zero on new hits (regenerate with `--baseline`); `pack.py` runs it
automatically (skip with `ZFIGHT=0`).

**The stub caveat, because it fails quietly:** every builder method `spec.py` calls must
exist on the stub. A missing one throws inside `build_all`, so the sweep stops covering
everything after that line. The stubs now carry a `__getattr__` no-op catch-all so a newly
added builder (as `tube3` was) can't silently truncate the sweep — but keep the explicit
recorders for `box`/`cyl`/`sph`, which must actually record.

## 7. What is verified vs assumed

**Verified** against renders or the drawings: roof geometry (both pitches, ridge heights,
eaves, everything derived from `mz()`/`wz()`); west-facade windows and the white cladding
panels around them (decided **once** in `build_f1_walls` — nothing downstream adds facade
panels, after a duplicate-panel defect was removed); floor-heating routing (19 circuits,
no self-overlaps or cross-clashes); basement furniture + tekninen tila; interior floor
textures; the anchor inventory (§4); and the terrace + everything on it, re-measured end to
end off `1krs_pohja50_1.pdf` (x/y) and the LÄNSI elevation (z), cross-validated plan ↔
elevation ↔ overlay to under 15 mm. The as-built terrace numbers live in `spec.py`;
re-derive with §5 rather than duplicating them here.

**Assumed / owner-sourced, needs confirmation:**

- **Pergola, clear sunroof, north terrace screen** (`T.perg.*`, `T.nscr.*`) — their
  existence, the screen covering only the *western* part of the north edge (the facade
  passage stays open), and the two infill materials come from the owner's photos. Exact
  head heights, mesh/lath sizes and frame widths are choices made here.
- **Tiered back garden** (`T.rsoil*`, `T.rveg*`, `T.rwall*`, `T.hedge.*`, `T.tramp.*`) —
  authored from description, no drawing. It renders as a too-regular grid of green domes
  and is the weakest part of the model; it needs a photo or a decision to simplify.
- **Site plan and `0krs_pohja50.pdf` are not fully mined** — the site plan is the right
  source for the driveway, plot boundary and back garden.

## 8. Textures

Exterior/wall maps are palette-matched Poly Haven CC0 scans; the **floors are procedural** —
`mktex.py` builds them from FFT-filtered Gaussian noise so every map tiles seamlessly in
both axes (the photo scans did not, and their repeat showed as straight lines across the big
basement). Run `python3 mktex.py` (needs `numpy` + `Pillow`) to regenerate `tex/` + a
`tex_sheet.png` contact sheet, then rebuild.

| material | map | authored square | reads as |
|---|---|---|---|
| `Wood` | `floor` | 3.0 m | matte-lacquered oak, 200 mm planks |
| `Tile` | `tile` | 2.4 m | matte porcelain 300×600 mm, running bond |
| `TileDark` | `tiledark` | 2.4 m | anthracite 300×300 mm porcelain (sauna / wet rooms) |
| `ConcreteDark` | `cdark` | 4.0 m | dark sealed concrete — kellari VAR1 |
| `ConcreteF` | `cfloor` | 4.0 m | pale concrete — kellari VAR2, TEKN, carport, terrace steps |
| `Slat` | `vboard` | 1.92 m | white 120 mm vertical boards — the facade panels |

Three rules keep these correct:

1. **Scale.** Floors get world-space UVs (`u = x_m·s`, `v = y_m·s`), so a map authored for a
   *T*-metre square must be listed in `TEXSETS` with `scale = 1/T` (`Wood` = 1/3.0, `Tile` =
   1/2.4). Walls take the other mapping (`u = (x+y)·s`, `v = z·s`) — image *columns* run
   along the facade, *rows* run up it. `vboard` stands its boards upright this way (stripes
   across the columns, 1.92 m square = 16 boards of 120 mm), and nothing varies along `v` so
   the vertical repeat is invisible at any panel height.
2. **Module division.** A tiled pattern's module must divide the 1024 px canvas exactly or
   the joints break at every repeat: 300×600 mm tiles are authored on a 2.4 m square
   (4×8 = 256/128 px), not 3.0 m (which needs 204.8 px tiles → a 4 px sliver). Normal maps
   are tangent-space OpenGL (+Y up), global strength 0.85.
3. **Ship size — normals at 512 px.** Diffuse maps ship at 1024 px; normals are downsampled
   to 512 (`mktex.py` does it in `save_nor`, `NOR_PX = 512`, *after* the derivative so the
   relief keeps its authored slope). This is most of why the set is 2.8 MB not 4.2 MB. It
   silently regressed to 1024 once and quietly added 1.45 MB to the GLB — **`ls -l
   tex/*_nor.jpg` over ~160 kB means it happened again.**

Where the facade boards *sit* is decided once, in `build_f1_walls`, next to the openings
they frame (`F1.slat.*` split at the strip-window sill/head, `F1.ent.*` around the front
door). Nothing downstream should add facade panels — a second eyeballed pair once z-fought
into a doubled, stepped column (§6 catches this).
