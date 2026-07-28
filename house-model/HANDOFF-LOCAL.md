# Working notes — Marmorikatu 10 house model

`README.md` next to this file is the contract: what the model guarantees to the mobile app.
This file is the workshop: where the source material lives, how it was measured, which
numbers are verified and which are still guesses, and the handful of tool quirks that will
otherwise cost an hour to rediscover. Nothing here is needed to *consume* the model — it is
needed to *change* it.

Everything runs locally. There is no cloud step, no session state, no network access
required at any point in the build.

---

## 1. Where everything is

| what | path |
|---|---|
| repo | `~/IdeaProjects/marmorikatu-home-automation`, model under `house-model/` |
| mobile app | `~/IdeaProjects/marmorikatu-mobile` (sibling repo, consumes the GLB + `cameras.json`) |
| drawings | `~/marmorikatu-dokumentit/` |

The drawings folder has four subfolders: `Arkkitehti piirustukset` (the architect set),
`Sähkö piirustukset` (electrical — the light positions in `build_lights` come from here),
`LVI pdf` (the floor-heating circuit diagrams behind `HEAT`), and `Mittakuvat`. The
architect PDFs that matter:

    0krs_pohja50.pdf            kellari plan
    1krs_pohja50.pdf            1. krs plan
    1krs_pohja50_1.pdf          1. krs plan, richer variant — prefer this one
    2krs_pohja50.pdf            2. krs plan
    julkisivut.pdf              all four elevations on one sheet
    Asemapiirustus 3.9.2009.pdf site plan — yard levels, carport, terrace footprint

Three DWGs sit alongside them. The original vector extraction that seeded `spec.py` came
from those; the PDFs are what everything has been checked against since.

## 2. The build loop

Edit `spec.py`, rebuild in Blender, pack, look at it.

```python
# Blender's Python console — one line, because globals do not survive between calls
ns={}; BASE='/Users/kyostiherrala/IdeaProjects/marmorikatu-home-automation/house-model'
exec(compile(open(BASE+'/bpy_backend.py').read(),'b','exec'),ns); ns['hk_run'](BASE); ns['hk_export'](BASE)
```

```sh
cd ~/IdeaProjects/marmorikatu-home-automation/house-model && python3 pack.py
```

`hk_run` prints the object count (**1720** meshes for the current spec) and `hk_export` writes
`marmorikatu.blend`, `marmorikatu-house.glb` and `marmorikatu-house.usdz`. `pack.py` builds
`out/marmorikatu-3d.html` and `out/cameras.json` and copies both up to the `house-model`
root, which is where git tracks them. `pack.py` does not need Blender; it reads the GLB.

Requirements are Blender 4.2+ (its bundled Python has `usd-core`, which the USDZ
post-process wants) and a system Python 3.10+ with `numpy` and `Pillow`.

## 3. Blender quirks that will bite

**Globals do not persist between `execute_blender_code` calls, or between console
invocations that re-exec the backend.** Every snippet has to be self-contained — re-exec
`bpy_backend.py` into a fresh namespace each time. This is why the rebuild is written as
one long line rather than three tidy ones.

**Image caching.** Blender keys textures by filepath, so after regenerating anything in
`tex/` a rebuilt scene will cheerfully keep showing the old bitmap. Force it:

```python
import bpy
for im in bpy.data.images: im.reload()
```

**`hk_export` is extremely loud** — roughly 137 KB of USD and glTF progress logging per
run. Over an MCP bridge that overflows the result-size limit and the call appears to fail
when it did not. **`contextlib.redirect_stdout` swallows it cleanly** (the older
`os.dup2(fd, 1)` trick does not, because BlenderMCP captures at `sys.stdout`, above the
file descriptor). Wrap the export and print only what you want back:

```python
import io, contextlib, os
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    ns['hk_run'](BASE); ns['hk_export'](BASE)
print('objects', len(bpy.data.objects), '| log', len(buf.getvalue()))
for f in ('marmorikatu-house.glb', 'marmorikatu-house.usdz'):
    print(f, os.path.getsize(os.path.join(BASE, f)))
```

This makes a rebuild-export-measure cycle a single bridge call, which is worth a lot.

**Engine name.** This Blender reports its EEVEE engine as `BLENDER_EEVEE`, not
`BLENDER_EEVEE_NEXT`. Scripts that set `scene.render.engine` need the former.

## 4. Reading the drawings

### 4.1 Elevations (`julkisivut.pdf`) — solved

All four elevations are calibrated. Scale is `S = 28.3465` PDF points per metre. Given a
point `(ptx, pty)` in PDF coordinates, the model coordinate is:

| elevation | horizontal | vertical |
|---|---|---|
| LÄNSI (west) | `x = (ptx − 177.2) / S` | `z = (289.80 − pty) / S − 0.08` |
| ETELÄ (south) | `y = (ptx − 882.81) / S` | `z = (289.80 − pty) / S − 0.08` |
| ITÄ (east) | `x = (547.5 − ptx) / S` | `z = (697.70 − pty) / S − 0.08` |
| POHJOINEN (north) | `y = 36.651 − ptx / S` | `z = (686.95 − pty) / S − 0.08` |

The `− 0.08` is the sheet's datum offset against z = 0 at the 1. krs floor. These four
formulas are what the roof geometry, the window heads and sills, and the cladding bands
were all derived from; they have been checked against the model repeatedly and hold.

> **`pty` in those four formulas is TOP-DOWN.** `pdfplumber`'s `line` objects give
> **bottom-up** `y0`/`y1`, so they must be flipped (`pty = page.height − y0`) before use.
> Feed bottom-up values straight in and every z comes out **mirrored about the datum** —
> silently, and looking entirely plausible: a cladding foot read that way as −0.673 is
> really +5.65, near the top of the wall. This is the same trap as `curve['pts']`
> (§4.3) but in the opposite direction, and it produced three confident wrong answers
> about the east cladding before it was caught.
>
> For the east elevation there is a cross-check that needs no flip at all, anchored on the
> KHH strip window (model x 7.040..7.541, z 0.740..2.160 — both reproduced exactly):
>
> ```
> x = (547.5 − ptx) / S            z = (pty − 146.52) / S      # pty = line.y0, bottom-up
> ```
>
> This lands the KHH window to 2 mm and both roof lines within 0.17 m. Two further
> windowing traps on this sheet: a `pty` window that only reaches z ≈ −1.8 cuts off the
> cladding steps, and a `ptx` window starting above ~50 discards the living wing entirely
> (model x 11.5..17.5 lives at ptx 51..221).

### 4.2 Plans — solved

For `1krs_pohja50_1.pdf` the scale is `S = 56.6795` pt/m and both axes are now confirmed:

```
plan_x = (ptx − 142.283) / S
plan_y = (795.79 − pty) / S
```

The earlier note claiming the y axis was unsolvable was wrong. The failure came from
picking the wrong two horizontal lines as the north and south exterior faces — the sheet
carries several full-width lines (dimension strings, the section marker, the title block
rule) with more ink mass than the actual wall faces. With the datum above, x and y both
reproduce the spec outline to a millimetre, and the terrace was measured end to end from
this sheet without ever reading a dimension string.

Plan is authoritative for x and y, elevation for z. There is a **systematic offset between
the two families of sheets: elevation x = plan x + 0.016.** Subtract 16 mm from any x taken
off `julkisivut.pdf` to land in model coordinates. Plan x maps to model x with no offset.

The plan extractor lives at `/tmp/plan.py` and is the exact analogue of the elevation one
(§4.1) — same both-endpoints-inside-the-band gotcha, same merge pass, same
ascending-sort output. It prints const-x runs, const-y runs and diagonals:

```sh
python3 /tmp/plan.py XA XB YA YB [minlen]      # e.g.  11.2 18.0 -5.2 0.20
```

**Drawing convention worth knowing before you measure anything clad:** cladding and louver
boards are drawn with **three lines per board** — the face plus two joint/shadow lines. The
three deltas (roughly 0.042 + 0.035 + 0.025) sum to the true 0.100 m module. Reading the
gaps as individual boards is what produced the wrong louver pitch in an earlier session.
Group the lines in threes and measure group-top to group-top.

**Hidden geometry is clipped.** Anything behind a railing or a solid parapet simply stops
at that line. On the LÄNSI elevation every window vertical in the terrace bay bottoms out
at exactly z = 0.798, which is the top rail — not a real sill.

### 4.3 Site plan (`Asemapiirustus 3.9.2009.pdf`) — solved

The only source for anything outside the building: ground levels, the plot, the driveway and
paving outlines, the carport position, the jäte enclosure. 1 page, 1684 × 2384 pt, 1:200,
and the **drawing is rotated ~165° on the sheet**, which is why it resisted for so long.

```
S  = 14.17064 pt/m        (nominal 1:200 = 14.17323, i.e. -0.018%)
th = 165.1710 deg         (direction of model +x in PDF space)

mx = -0.0682181*ptx + 0.0180610*pty -   1.7721
my = -0.0180610*ptx - 0.0682181*pty + 139.5134
```

Fitted on the seven heavy (lw = 1.44) footprint edges: **rms 1.9 mm, max 3.2 mm**. The
2.70 × 3.30 notch at the (16.98, 0) corner is on the sheet exactly as `P1` has it, which
fixes the axis directions unambiguously. Independent check: the drawn stair nosings
reproduce `STAIR_X` to ≤ 3 mm.

**Datum: 1. KRS +135.90 = model z 0, so model z = absolute − 135.90.** Not +136.00.
Confirmed three ways off the sheet's own building levels — KELL +132.86 → −3.04,
KATOS 1AP +135.35 → −0.55, TR/VAR +135.85 → −0.05.

Linework weights are how you tell surfaces apart, because **there is no fill or hatch
anywhere on the sheet** (`fill=False` on all 4590 curves): 1.44 = building walls, 0.96 =
dashed eaves/roof lines, 0.72 = site linework (paving, slab edges, enclosures), 0.48 =
plant symbols and text. Paving is a single unbroken 0.72 polyline with R 1.20 m fillets —
the fillets are the giveaway, nothing else in the front yard is filleted.

**Two extraction traps, both of which produce plausible-looking garbage rather than an
error.** They cost an hour each:

- `pdfplumber` line objects are **bbox-normalised** (`x0<x1, y0<y1`), so segment direction
  sign is lost. A line whose bbox angle reads +14.83° may really be at −14.83°. Classify by
  |angle| against the two families (14.834° / 75.166°) and reconstruct the true endpoints —
  choosing wrong turns the building into a 60° parallelogram and rotates the frame by 2×14.83°.
- `curve['pts']` uses **top-down** y while `lines` use **bottom-up** `y0`/`y1`. Flip curve
  points by `page.height` or the two families land in different places.

`Tontti/Vaaituskartta.pdf` is a pure raster scan — 0 chars, 0 lines, 0 rects, 0 curves.
Nothing extractable at all. There is no landscape plan anywhere in the archive.

**Where the sheet is overridden.** It is a 2009 permit drawing, so as-built features beat
it and the owner beats both. Currently overridden: the tiered back garden (no terracing and
not one retaining wall anywhere on the plot), the −1.70 at the carport's far corner
(9.50, −8.02) — the ground is flat there and the shelves hold the change — and the front
entrance bed, which the sheet draws as plain paving. All three are noted in `spec.py`.

### 4.4 Why you cannot just read the text

All three architect plan PDFs report `chars = 0`. The dimension strings are outlined vector
art, not text, so no extractor will ever return them; they have to be read off a rendered
image by eye. Similarly `page.lines` alone misses most of the geometry — decomposing
`page.curves` as well takes the 1krs sheet from 2281 segments to 56453. If a geometry sweep
comes back suspiciously sparse, that is why.

### 4.5 Overlay recipe — the actual checking tool

The reliable way to verify a change is to render the model on a transparent-ish background
and composite it over the drawing at a known pixel scale, with the drawing's ink recoloured
so the two are distinguishable.

Render the PDF page with PyMuPDF at the pixel scale you want:

```python
import fitz
PXM = 100                       # target pixels per metre
sc  = PXM / 28.3465             # S = pt per metre
pix = fitz.open(pdf)[0].get_pixmap(matrix=fitz.Matrix(sc, sc))
```

Crops that line up with the elevation cameras below:

| view | px/m | crop origin | crop size |
|---|---|---|---|
| LÄNSI | 100 | (494, 144) | 1960 × 1100 |
| ETELÄ | 150 | (4477, 562) | 1560 × 1200 |

Then tint the drawing's ink over the render:

```python
ink = pdf_gray < 170
out[ink] = out[ink] * 0.15 + np.array([255, 40, 40]) * 0.85
```

Red lines are the drawing, everything else is the model. Misalignment is obvious at a
glance in a way that comparing two numbers never is.

### 4.6 Elevation render camera

The west (LÄNSI) check view, which is the one most often needed:

```python
cam.data.type      = 'ORTHO'
cam.data.ortho_scale = 11.0
cam.data.clip_start  = 29.85
cam.data.clip_end    = 60
cam.location         = (5.30, -30.0, 1.45)
cam.rotation_euler   = (radians(90), 0, 0)
scene.render.engine  = 'BLENDER_EEVEE'
```

The near clip at 29.85 is deliberate: it slices the near wall away so the camera sees the
facade it is pointed at rather than the one nearest it. The scene camera is named `Cam`.
Renders go to `preview/`, which is scratch — nothing there is part of the contract.

## 5. The builder API

`spec.py` never touches `bpy`. It calls methods on a builder object, which is why the
checking scripts can replay the whole spec in a plain shell against a recording stub. The
methods, all on `BlenderB` in `bpy_backend.py`:

```
polyseg(name, segs, w, z0, z1, mat)            extruded polyline band
tube(name, pts, r, z, mat, sides=8)            round swept tube — the heating pipes
box(name, xs, ys, zs, mat)                     xs/ys/zs are (min, max) pairs
cyl(name, x, y, z0, z1, r, mat, segs=20)
sph(name, x, y, z, r, mat)
slab(name, poly, z0, z1, mat, holes=None)
room(name, poly, mat, z=0.0, holes=None)       the Room_ floor patches
roofquad(name, pts, thick, mat)                pts are the TOP surface; extrudes DOWNWARD
prism(name, x0, x1, poly_yz, mat, axis='x')    axis='x' → profile in (y,z); 'y' → (x,z)
```

Two traps. `roofquad` takes the top surface and grows the slab downward, so a roof deck
described by its underside will sit one thickness too high. `prism` builds a single n-gon
and extrudes it, so a concave profile comes out wrong — split it into convex pieces.

Above these sit the spec's own helpers: `W`/`wall_x`/`wall_y` for walls with openings,
`face` for window and door frames, and the furniture shorthands `bed`, `table`, `chair`,
`wardrobe`, `rug`, `plant`, `sofa`, `toilet`. The builders are `build_kellari`,
`build_krs1`, `build_krs2`, `build_roof`, `build_katos`, `build_lights`, plus the
floor-heating routing machinery from `_area` down (that block is a small autorouter —
`_lanes` lays serpentine lanes inside a room polygon, `_field_path` walks them, `_link`
joins fields, `_fillet` rounds the corners; `HEAT` at the top holds the per-circuit
metadata and `HEATAXIS` overrides the lane direction for four circuits).

Key constants at the top of `spec.py`: wall thicknesses `EXT = 0.30` (exterior),
`KXT = 0.34` (kellari concrete + render), `INT = 0.10`; storey heights `H_K = 2.54`,
`H_1 = 2.56`, `H_1E = 3.01`, `H_L = 2.60`, `H_2 = 2.58`, `H_2E = 3.19`; floor datums
`Z_K = −3.04`, `Z_2 = 3.01`; and the three outline polygons `P1`, `PK`, `P2`.

## 6. Checking scripts

`check_dupes.py` is the one that exists and works — see README §8 for how to run it and
what its two known-benign hits are. It replays `build_all` against a stub class that
records boxes instead of building them.

Three others were written during earlier sessions and have not survived into the repo:
`check_overlaps.py` (in the container, but stale — it predates `tube` and `polyseg` and
dies with `AttributeError: 'CB' object has no attribute 'tube'` until those stubs are
added), and `check_outer.py` / `check_cross.py`, which are gone entirely. They are cheap to
rewrite from `check_dupes.py`; the stub class is the whole trick.

The stub caveat is worth repeating because it fails silently: **every builder method
`spec.py` calls must exist on the stub, even as a no-op.** A missing one throws inside
`build_all`, the sweep stops covering everything after that line, and the script still
prints a confident "0 pairs".

## 7. Git

New commits only — never amend, never rebase, and **never push**. Pushing and enabling
GitHub Pages are the owner's to do.

Commit trailers:

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012bXNeRimQW1vF3SmVjPV9R
```

**The repo shows about 50 files as modified that nobody touched.** They are CRLF↔LF
conversions from a checkout on a different platform. They must never be swept into a
commit. Consequently: never `git add -A`, never `git add .`, never `git commit -a`. Stage
explicit `house-model/...` paths and nothing else.

## 8. State — what is settled and what is not

Settled, and re-verified against renders or the drawings: the roof geometry (both pitches,
ridge heights, eaves, and everything derived from `mz()`/`wz()`); the west-facade windows
and the vertical white cladding panels around them (a duplicate-panel defect was found and
removed here — the panels are now decided once, in `build_f1_walls`, and nothing downstream
adds facade panels); the floor-heating routing, 19 circuits totalling 1077 m with no
overlaps; the basement furniture layout and the tekninen tila equipment; the interior floor
textures; and the anchor inventory the app depends on — 25 `Room_` patches, 41 `Light_`
anchors, 19 `Heat_` circuits, which `pack.py` prints on every run.

The **terrace and everything on it** — re-measured end to end off `1krs_pohja50_1.pdf`
for x and y and the LÄNSI elevation of `julkisivut.pdf` for z, then cross-validated three
ways (plan ↔ elevation ↔ rendered overlay). Every level agrees to under 15 mm. The full
dimension table is §9 below.

Open:

**The pergola, the clear sunroof and the north terrace wall are real, and they are
owner-sourced, not measured.** A previous pass deleted the pergola after concluding that the
LÄNSI horizontals it had been reverse-engineered from were something else — and that reading
of the elevation is still correct: z = 2.128 is the window **sash** heads (the gaps between
runs are mullions), z = 2.160/2.206 is the continuous frame/lintel band already modelled as
`F1.head.notch`, z = 2.322–2.643 is the set-back notch facade plus the wing-roof fascia
`R.fascia.s` at 2.603–2.743, and every vertical in that band bottoms out at exactly z = 0.798
(the top rail) because of hidden-line clipping. But the conclusion drawn from it — that
therefore no pergola exists — was **wrong**. The owner states the pergola, its clear sunroof
and a wall closing the north end of the terrace all exist; they simply post-date or are absent
from the 2009 drawings. **The owner outranks the drawings.** Absence from a sheet is evidence
about the sheet, not about the house — the same will be true of anything else built since 2009,
so treat "not on the drawing" as a question to ask, never as a licence to delete.

They have been re-authored and re-fitted to the corrected deck (x 11.638–16.980, not the old
8.70–16.98). Now in `spec.py`, immediately after the `T.rail.s.top` box:

- `T.perg.post0..2` — three posts standing on the west railing posts `WP[1:]`, y −3.396…−3.287,
  z 0.798 → 2.02. There is no post on `WP[0]`: `T.nscr.post0` (below) continues the west railing
  post upward on exactly that line and carries the north end of `T.perg.beam1`.
- `T.perg.beam1` (outer, x 11.638–16.980, y −3.396…−3.256, z 2.02–2.14) and `T.perg.beam2`
  (inner, same x, y −0.20…−0.06, z 2.36–2.48).
- `T.perg.wpost0..2` on `[WP[0]] + WP[2:]` — the inner beam runs the full length but the west
  facade only exists for x ≤ 14.28 (plan `P1` jogs there), so across the open notch bay two of
  these stand under it instead of a wall. `wpost0` on `WP[0]` is the north-east corner post
  beside the entrance: with the old solid north wall gone, nothing else held that end of
  `T.perg.beam2` and it cantilevered in mid-air.
- `T.perg.canopy` — a `roofquad` falling west from 2.51 at y = 0 to 2.18 at y = −3.45, 30 mm
  thick, material `'Canopy'`. Clearance under `R.wing.s` is 466 mm at the facade and 300 mm at
  the y = −0.699 eave.
- `T.perg.raf0..8` — nine rafters at 0.625 m pitch from x = 11.85. They are `roofquad`s, not
  boxes, so their top face **is** the canopy underside plane `canopy_z(y)`; a constant-z box
  rafter breaches the sheet at the low end.
- `T.nscr.*` — the north **screen**, on the west-railing post line `WP[0]` = x 11.701–11.816.
  Frame in `'White'`: `post0` (y −3.396…−3.287, z 0.798 → 2.02, continuing `T.rail.w.p0`),
  `post1` (y −1.595…−1.480, full height), `head` (z 1.90–2.02, tucked under `T.perg.beam1`),
  `mid` at the rail line (z 0.703–0.798) and `sill` (z −0.193…−0.103). `glaze` is the corrugated
  translucent infill between sill and mid rail, material `'Railing'`, inset 30 mm each side.
  Above the rail line `lv1..8` × `lh1..5` form a square trellis, 24 mm laths on a ~180 mm mesh.

**This replaced `T.nwall` / `T.nwall.cap`, which were wrong.** Those were authored as a solid
100 mm `'Slat'` prism running the *full* width y −3.396…0 — which walled off the terrace
entrance. The owner's photographs show the truth: the screen closes only the western part of
the north edge, and **the passage along the facade (y −1.480…0, the band `T.strip` occupies)
is completely open**, with two potted thujas standing in it and the driveway visible beyond.
Photographs also settle the infill: corrugated translucent sheet in a white frame under an open
white trellis, not vertical boarding.

The old `T.perg.lattice` (x 8.72–8.78) was **not** restored — it was a stand-in at the old
deck's north end and the screen supersedes it.

**Screen dimensions remain assumption.** Its existence, its position (western part only), the
open passage and the two materials come from the photographs. The exact head height (2.02, set
to tuck under the pergola beam), the 180 mm mesh, the 24 mm laths and the 115 mm frame (taken
from `railpost`) are all choices made here and the owner should confirm them.

`T.pave.side` is a related fix from the same pass: a wedge of `T.lawnW.slab` was showing
between the carport and the terrace because `TR.drive.slab.b/c` stop at x 9.30/8.70. It is brick
in reality, so a `'Paver'` slab now runs x 8.70–11.638, y −5.30…0, **flat** at `Z_GRADE`
(z −0.750…−0.680, 10 mm proud of the turf), with a notch at x 8.70–9.30, y −5.10…−3.40 keeping
it clear of `TR.drive.slab.b`. It was first built as a `roofquad` raking −0.56 → `Z_GRADE` to
meet the drive; the owner rejected that — it put a ramp where the ground is flat and stood the
paving at the wrong height along the terrace. The rule that settles it: this paving replaces
lawn that was **already** at `Z_GRADE`, so laying it flat at `Z_GRADE` adds no level change
anywhere. The lawn slab is left underneath as sub-base.

**Ground under and beside the terrace — two separate falls, and nothing east of y = 0.**
`T.ground` (y −3.396…0) follows the deck fall `Z_GRADE → Z_YARD`; it is hidden behind the
`T.skirt.b*` louvers. `T.ground.stair` (y −4.70…−3.396) carries the outdoor flight and is
dropped 500 mm lower, running `Z_GRADE − 0.60 → Z_YARD − 0.05`. The drop is not cosmetic: the
1.074 m landing shifts the stair out of phase with any straight rake, so a single ground plane
across the whole width buried every second tread — the owner reported the stair as "half
missing". `T.gstep*` are also 550 mm deep (was 220 mm) so consecutive treads overlap into one
solid stepped mass with a concrete cheek, matching the photograph. Verified by ray-cast: all
15 treads read at exactly `tread_z(k)`, worst clearance over `T.ground.stair` is 64 mm.

There is deliberately **no** ground east of y = 0. A `T.ground.n` quad once covered
x 14.28–16.98, y 0–3.30; that is the open notch, decked over by `T.deck` with the basement
underneath, so it drove a sloping `ConcreteF` slab straight through `Room_kellari_VAR2`. Deleted.
The check that proves it is gone: ray-cast **downward from z = −0.60** (i.e. from under the
deck) inside the notch — it must hit nothing, and the only `ConcreteF` left intersecting the
basement volume must be `Room_kellari_VAR2` itself.

**Verification lesson worth keeping.** Two round-trips were wasted eyeballing perspective
renders that appeared to still show grass. What settled it was not a better camera angle:

- `scene.ray_cast(dg, Vector((x, y, 14.0)), Vector((0, 0, −1)))` on a grid, reading back
  `obj.data.materials[0].name` — this proves which material is actually visible from above.
- Then an **exact ortho plan render** with a known px/m mapping: `cam.data.type = 'ORTHO'`,
  `ortho_scale = 14.0`, `clip_start = 1.0`, `clip_end = 60.0`, `location = (12.0, −4.0, 20.0)`,
  `rotation_euler = (0, 0, 0)`, 1400×900 → covers x 5.0..19.0, y −8.5..0.5 at exactly 100 px/m,
  image up = +y, image right = +x. Saved as `preview/check_pave_plan.png`.

For any "is this surface covered / does this overlap" question, use those two. Perspective views
are for judging whether something *looks* right, never for deciding whether it *is* right.
`preview/check_pergola.png` (PERSP lens 34, loc (19.5, −13.0, 2.2), target (13.0, −1.6, 0.9)) is
the best single view of the pergola and the north wall.

The **tiered back garden** (`T.rsoil*`, `T.rveg*`, `T.rwall*`, `T.rpl*`, `T.hedge.*`, and
`T.tramp.*`) is not from any drawing — it was authored from description. It renders as a
very regular grid of green domes and is the weakest-looking part of the model. It needs
either a photograph or a decision to simplify it.

`Asemapiirustus 3.9.2009.pdf` (site plan) and `0krs_pohja50.pdf` are still unexamined. The
site plan is the right source for the driveway, the plot boundary and the back garden.

Texture **normal maps must ship at 512 px** (README §7). They silently regressed to 1024
once already, which cost 1.45 MB of GLB. `mktex.py` now enforces it via `NOR_PX`, but a
texture generated by another route could reintroduce it — `ls -l tex/*_nor.jpg` showing
anything much over 160 kB is the symptom.

## 9. Terrace — measured dimensions

All values are model coordinates: x runs 0 → 16.98 north to south, y runs 0 → 7.98 west to
east (so the terrace sits at negative y, west of the house), z = 0 at the 1. krs floor.
Sources are `1krs_pohja50_1.pdf` for x/y and the LÄNSI elevation for z, calibrated per §4.

**Levels.** Deck top −0.193 (193 mm below the 1. krs floor, not the 30 mm the old model
used). Entrance-strip fascia underside −0.348. Wide-deck fascia underside −0.493, i.e. a
300 mm edge beam. Finished grade along the west wall −0.690. Basement yard, at the foot of
the outdoor stair, −3.361.

**It is two decks, not one.** A 1.48 m entrance strip runs along the west wall at
x 3.906–11.638, y −1.480–0. The wide deck is x 11.638–16.980, y −3.396–0 and also fills the
roofed notch at x 14.28–16.98, y 0–3.30. The old block ran a single deck from x 8.70 and
sat 163 mm too high.

**Two step rings** wrap the strip down to grade. Each is 300 mm wider and 300 mm longer
than the one above — the earlier note that they wrapped only at the north corner was wrong,
and the mirrored return verticals on the elevation (x 3.323/3.623/3.905 north,
11.053/11.352/11.635 south) prove it. Ring 1, top −0.348: x 3.607–11.338 at y −1.780,
turning south at x 11.338 to y −3.097. Ring 2, top −0.535: x 3.307–11.038 at y −2.080,
turning south at x 11.038 to y −3.396. Then grade at −0.690.

**Front door** opening x 4.886–6.197 at y = −0.030, sill top z −0.119. The second west
opening, the kitchen strip window, is x 8.342–9.443.

**Railing.** Infill z −0.172 to 0.686 — nine slats, 58 mm face at 100 mm pitch, slat *k*
top = 0.686 − 0.100 k. White top rail 0.703–0.798. Posts are 115 mm. West rail: infill
y −3.370–−3.303, posts at x 11.701–11.816, 13.400–13.517, 15.101–15.216, 16.802–16.917.
South rail: infill x 16.886–16.954 over y −3.396–3.301, posts at y −3.396–−3.281,
−1.632–−1.517, 0.067–0.183, 1.768–1.882, 3.186–3.301.

**Outdoor stair**, cross-validated plan against elevation to 15 mm: sixteen risers of
166.9 mm at 300 mm going, 1.20 m wide (y −4.596–−3.396), with a 1.074 m landing after the
eighth. Riser x positions are 11.638, 11.938, 12.238, 12.537, 12.839, 13.139, 13.439,
13.739, then the landing, then 14.813, 15.113, 15.413, 15.713, 16.015, 16.315, 16.615,
16.915. Top −0.690, landing −2.026, foot −3.361.

**Under-deck louver skirt.** Boards 79 mm face at 100 mm pitch, top board top −0.535, all
ending at x 16.674. Each board's left end is the stair riser at which the stair has fallen
below it — `k = ceil((−0.690 − z_bottom) / 0.166938 − 1e−6) − 1`, then `x0 = STAIR_X[k]`,
or 11.876 when k < 0. That stepped left edge is drawn on the elevation and is what pins the
stair to the skirt. End posts 240 mm at x 11.638–11.876 and 16.674–16.914. A 42 mm downpipe
at x ≈ 16.965.

**Entrance canopy.** Plan outline x 3.936–7.148 running out to y −1.151; posts
x 4.236–4.351 and 6.731–6.848 at y −0.782–−0.667. From LÄNSI: top 2.912 at the wall falling
to 2.472 at the tip, underside 2.703 → 2.263, so a 209 mm deck. Verified against the
rendered model to 8 mm.

**Notch / covered loggia** x 14.28–16.98, y 0–3.30. Already roofed by `R.wing.s`; fascia
2.603–2.743 matches the drawn 2.622/2.643 pair, and the head band `F1.head.notch` at
2.160/2.206 matches the drawing exactly. Nothing needed adding here.

**West grade slab.** `T.lawnW.slab` covers x −2.80–11.638, y −5.30–0 at −0.690, L-shaped so
it butts `T.lawnN.slab` along y = −0.30 rather than z-fighting with it. Without it the
kellari wall stood exposed along half the west facade.

**Checking these numbers again.** The two saved check renders are `preview/check_lansi.png`
(west ortho: `ortho_scale` 11.0 → use 15.5 to include the terrace, `clip_start` 25.0,
location (10.25, −30.0, −0.20), rotation (90°, 0, 0), 1600 × 800) and
`preview/check_terrace.png` (perspective, 32 mm, from (26.0, −16.0, 6.5) looking at
(13.5, −1.2, −0.6)). The ortho one is directly comparable to a PDF crop rendered at
103.2 px/m over x 2.5–18.0, z −4.075–3.675; a column-wise edge diff between the two is what
confirmed every level above. Note that a horizontal ortho camera can never show flat ground
burying a wall — the ground slab is only 120 mm thick and is seen edge-on — so use the
perspective view to judge terrain.
