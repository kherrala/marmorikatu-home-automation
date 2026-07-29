# Marmorikatu 10 — interactive 3D house model

Built from the architect drawings (`0krs/1krs/2krs_pohja50` DWG-vector extraction, `julkisivut`
elevations), the electrical drawings (`1/2 krs valaistus` — light positions), and the owner's
photos. Levels +132.86 / +135.90 / +138.91, block ridge +143.40, living-wing ridge +140.40.

## Files (house-model/)

| file | purpose |
|---|---|
| `marmorikatu.blend` | Blender scene — rebuild after editing `spec.py` (see `MODELING.md`) |
| `marmorikatu-house.glb` | The model (~7.1 MB, textured, y-up glTF) — Android/web |
| `marmorikatu-house.usdz` | Same model for iOS/SceneKit (USD, Y-up, names preserved) |
| `marmorikatu-3d.html` | Same viewer fully self-contained (offline / WebView-ready) |
| `cameras.json` | Generated per-room camera presets + light anchor positions |
| `spec.py` / `bpy_backend.py` | Parametric source of truth |
| `viewer_template.html` / `pack.py` | Viewer template + packer that builds `marmorikatu-3d.html` |
| `vendor/three.min.js` | three.js r128, inlined into the viewer by `pack.py` so no build step needs the network |
| `tex/*.jpg` | PBR textures + tangent-space normal maps: exterior set (Poly Haven CC0, palette-matched: siding, pavers, concrete, brick, lawn) and the procedural set (`floor` oak, `tile`/`tiledark` ceramic, `cfloor`/`cdark` basement concrete, `vboard` white vertical cladding) |
| `mktex.py` | Generates the procedural floor textures (see `MODELING.md` §8) |
| `check_dupes.py` | Duplicate-geometry sweep (see `MODELING.md` §6) |
| `out/` | Packer staging area; the tracked copies live at this level and `pack.py` refreshes them |
| `preview/` | Scratch renders. Not part of the contract; safe to delete |

Rebuilding the model, changing geometry, and regenerating textures are maintainer tasks —
the build loop, the `BlenderB` builder API and the drawing calibrations live in
[`MODELING.md`](MODELING.md). The native-renderer sun/fill/ambient light rig is in
[`LIGHTING.md`](LIGHTING.md). Everything below is the **consume-time contract**: what the
model guarantees to whatever renders it.

---

# Technical contract for the mobile app (Kotlin Multiplatform / Compose)

## 1. Coordinate system

glTF/three.js: **x** runs pohjoinen→etelä along the house (0…16.98), **y** is up with
0 = 1. krs floor (+135.90; kellari −3.04, 2. krs +3.01), **z** = −(plan west→east), i.e.
the terrace side is +z, the itä facade −z. All distances in meters. Yard levels per the
asemapiirustus: entrance yard/carport bay −0.55 (+135.35), street corner −0.78 (+135.10),
VAR floor −0.05 (+135.85), SW terrace yard −3.00 (+132.90).

**Roof.** Both roofs are the same 1:3 gable (18.44°) folding about plan y = 3.990, taken
off `julkisivut.pdf`. Deck top at the ridge is 7.501 over the two-storey block and 4.503
over the living wing — exactly 3.000 m apart — with eaves at 5.938 / 2.940 on the y =
−0.699 and 8.681 lines and a 0.197 m vertical deck thickness throughout. Everything that
lands on a roof (wall bands, fascias, barge boards, gutters, standing-seam ribs) is
derived in `build_roof` from the two `mz()` / `wz()` height functions, so changing a
ridge moves the whole assembly with it.

## 2. Node hierarchy (the visibility API)

```
Talo
├── Kellari | Krs1 | Terassi | Katos | Krs2 | Katto          ← floor groups
│   ├── <G>_seinat_ulko   exterior walls (dollhouse: hide)
│   ├── <G>_seinat_sisa   interior partitions
│   ├── <G>_lasit         window glass
│   ├── <G>_ovet          door leaves (hide for open-plan view)
│   ├── <G>_lattia        slabs / decks
│   ├── <G>_huoneet       Room_* pick patches (4 mm above floor)
│   ├── <G>_portaat       stairs (upper U-flight lives in Krs2)
│   ├── <G>_kalusteet     furniture
│   ├── <G>_valot         Light_* fixtures (see §4)
│   └── <G>_lammitys      Heat_* floor-heating circuits (see §4b; hide by default)
```

Floor modes used by the reference viewer: `kellari→[Kellari]`,
`krs1→[Krs1,Terassi,Katos]`, `krs2→[Krs2]`, `all→everything`. `Katto` (roof, incl. the
wing's flat white ceiling) is a separate toggle so top-down views are never occluded.
There are **no baked ceilings** in floor groups. Explode view = translate whole groups in +y
(viewer uses offsets ×`{Kellari:0, Krs1/Terassi/Katos:1, Krs2:2, Katto:3}`).

## 3. Room picking

Every room has a flat patch mesh named `Room_<kerros>_<huone>`, e.g. `Room_1krs_OH`,
`Room_2krs_MH2`, `Room_kellari_VAR1`, `Room_katos_AUTOKATOS`. Raycast against meshes whose name
starts with `Room_` (they sit 4 mm above the floor so they always win the ray against the slab).
The open-plan wing has **no walls** between kitchen, dining and living, but it is deliberately
split into three zones — `Room_1krs_KT`, `Room_1krs_RUOKAILU`, `Room_1krs_OH` — so each area can
be focused and its lights (`Light_1krs_KT`/`Light_1krs_SAAREKE`, `Light_1krs_RUOKAILU`,
`Light_1krs_OH`) controlled independently.
Highlight recipe used in the viewer: clone material, set `emissive=#2563eb`,
`emissiveIntensity≈0.35`. The `<kerros>` token (kellari/1krs/2krs/katos) matches the
home-automation floor naming, so `set_lights_by_floor` / room states map 1:1.

## 4. Lights (from the electrical drawings)

Each fixture is an individually named mesh — the on/off **anchor**:

```
Light_<kerros>_<huone>[_n]     interior     e.g. Light_1krs_KT, Light_2krs_AULA_2
Light_ulko_*                   outdoors     etuovi_1/2, tekn, terassi_1/2, parveke,
                                            katos, piha_1..3 (lanterns on the skirt)
Light_katos_*                  carport      katos_1/2, katos_VAR
```

41 anchors total; positions per the valaistus drawings. **LED groups on one switch are
one anchor with `.pN` sub-head meshes** that light together: `Light_1krs_OH` (3×2 grid),
`Light_1krs_KT` (4 in series; the two island pendants are their own `Light_1krs_SAAREKE`),
`Light_1krs_KHH` (3×2), `Light_1krs_PH` (2×2), `Light_2krs_AULA` (4 in series, plus a
separate `Light_2krs_AULA_KATTO` ceiling lamp), `Light_1krs_IKKUNA` (decorative lights
over the wing windows), `Light_kellari_VAR1_2` (3 pendants over the billiard),
`Light_2krs_MH2` (2 heads) and `Light_ulko_parveke` (2 heads on the balcony).
Other sub-parts (cords, poles) also carry a `.` suffix — the anchor is always the
dot-free name. `cameras.json → lights` lists every anchor's world position for placing
tap targets or badges without traversing the scene.

**Fixture visibility.** Every fixture mesh lives in its floor's `<G>_valot` group
(`Kellari_valot`, `Krs1_valot` (incl. facade sconces), `Krs2_valot`, `Terassi_valot`,
`Katos_valot`), so the whole layer can be hidden with one `getChildByName(...).isVisible`
flip. The reference viewer goes one step further and the app should copy it: **all fixture
meshes are hidden by default and a fixture only becomes visible while it is lit** —
`setLightByName(name,true)` reveals the fixture + glow, `false` hides it again. Force them
all visible with the *Valaisimet* checkbox, `?fixtures=1`, or `setFixturesVisible(true)`
(exposed on `window` for the WebView bridge).

**On/off rendering recipe (what the viewer does, works on mobile):**
on: `material.emissive=#ffe9b0`, `emissiveIntensity≈1.5`, plus one additive-blended sprite
(radial-gradient texture, scale ≈1.15, `depthWrite=false`) just below the fixture.
off: intensity 0, sprite hidden. This is per-fixture stateful and costs no real lights.
If you want true illumination for a *focused* room, attach ONE `PointLight` (intensity ~6,
distance ~6) at the anchor of the room you're viewing — never all 41 at once.
Viewer URL params for testing: `?lights=1` (all on), tap any fixture to toggle it.

This is *fixture* state (a lamp's own glow). The scene's sun/fill/ambient rig that lights
the whole model — the directions, ratios, and per-engine Filament/SceneKit setup — is a
separate concern in [`LIGHTING.md`](LIGHTING.md).

## 4b. Floor heating (lattialämmitys)

Every thermostat-regulated underfloor loop from the LVI *Lattialämmitys* sheets is a flat
overlay mesh **`Heat_<kerros>_<nn>`** (4 mm prism, 42 mm above the floor so it clears the
Room_ patches and rugs), grouped under its floor's `<G>_lammitys` node. It is an
inspection layer: **keep `_lammitys` hidden by default**, exactly like the viewer's
*Lämmitys* checkbox (`?heat=1`). First digit of the circuit number = jakotukki
(manifold): JT1/JT2 in the kellari by the VAR1/VAR2 doorway, 3x/4x on the 1. krs
(JT4 sits by the kitchen tall units), 5x upstairs.

| piiri | kerros | alue | lenkki |
|---|---|---|---|
| 11 | kellari | VAR2 eteläosa | 61 m |
| 12 | kellari | VAR2 pohjoisosa | 69 m |
| 21 | kellari | VAR1 länsikaista | 56 m |
| 22 | kellari | VAR1 | 62 m |
| 23 | kellari | VAR1 | 67 m |
| 24 | kellari | VAR1 itäkaista | 74 m |
| 31 | 1krs | LH + PH | 35 m |
| 32 | 1krs | KHH + VH | 56 m |
| 33 | 1krs | ET + TK + VH2 + WC + TEKN | 70 m |
| 34 | 1krs | MH | 57 m |
| 41 | 1krs | KT | 42 m |
| 42 | 1krs | RUOKAILU | 55 m |
| 43 | 1krs | OH länsiosa | 56 m |
| 44 | 1krs | OH itäosa | 48 m |
| 51 | 2krs | MH + VH | 64 m |
| 52 | 2krs | MH2 | 52 m |
| 53 | 2krs | AULA | 61 m |
| 54 | 2krs | MH3 | 70 m |
| 55 | 2krs | KPH | 22 m |

**Hot/cold rendering (copy the viewer's recipe):** clone the patch material per circuit
and render it flat: `color` black, `emissive` = lerp cold `#3b82f6` → hot `#ef4444`,
`emissiveIntensity 1.0`, env-map off — a flat emissive overlay stays saturated under any
lighting/tone mapping; values 0..1, booleans map to 0/1. Tapping a visible circuit
toggles it. Viewer/WebView API: `setHeatByName('Heat_1krs_41', true)` (bare `'41'` works
too), `setHeatingVisible(true)`; URL `?heat=1&hot=41,53,11` restores layer + hot set.
`cameras.json → heating` lists every circuit's `circuit`, `floor`, `center`/`size`
(three.js frame), served `rooms` and `loop_m` — place badges or map thermostat telemetry
(e.g. the home-automation MCP room temperatures) onto circuits without traversing the
scene. Note that several circuits share one room (OH is split west/east, VAR1 into four
bands): heat is addressed per **circuit**, not per room, exactly as the manifold is. Base
material is `HeatOff` (neutral `#8E9AA8`) so an uncolored patch reads "no data". On iOS
the names survive as-is (`Heat_1krs_41` is dot-free); color via `SCNMaterial.diffuse` +
`emission`.

**Pipe view (the drawn loops).** Each circuit also carries its serpentine as
**`Heat_<kerros>_<nn>.pipe`** — one mesh, now a **swept round tube**: an 8-sided Ø22 mm
profile (r = 11 mm) mitred through every bend and centred at slab z+0.057, so the loops
read as pipe rather than as a flat ribbon and the corners join cleanly instead of
overlapping. Geometry follows the sheets: runs at drawing c/c (the sheet's line is a
supply+return pair, so c/c = 2·area/lenkki), run orientation per sheet, plus the feed
tail to its jakotukki. The routing is checked against the drawings on every build —
**0 self-overlaps within a circuit and 0 clashes between circuits**; if you edit a loop
in `spec.py`, re-run that check before shipping. The five manifolds are
`Heat_<kerros>_JT1…JT5` boxes (JT1/JT2 on the kellari divider, JT3 in TEKN, JT4 by the
kitchen, JT5 by the upstairs stair); `cameras.json → manifolds` has their positions.
The viewer's Lämmitys select switches
**Alueet / Piirit / Molemmat** (zones solid · pipes with a 10 % zone hint · zones at 30 %
+ pipes; the zone patch stays the tap target in every mode): URL `&hv=a|p|b`, bridge
`setHeatView('areas'|'pipes'|'both')`. Mirror it on mobile: areas = patch, pipes =
`.pipe` sub-mesh (`_pipe` after USD dot-sanitation, `_JT*` boxes ride with the pipes),
same color lerp on both. The lenkki values in the table are the as-built loop lengths
from the sheets.

## 5. Camera presets & transitions

`cameras.json` (regenerated by `pack.py` on every export) contains for every room:

```json
"Room_1krs_LH": {"center":[...], "size":[...],
                 "orbit": {"target":[x,y,z], "radius":r, "phi":0.55}}
```

Orbit model (same as the viewer): `position = target + r·(sinφ·cosθ, cosφ·secθ→cosφ, sinφ·sinθ)`
— i.e. spherical angles θ (yaw) and φ (polar), radius r, look-at target. A floor preset is the
bbox of its visible groups: target = bbox centre, `radius = max(size.x, size.z)·1.35`,
φ≈0.6–1.05. Suggested per-room framing: φ=0.55, radius = `max(3.5, max(size.x,size.z)·2.1)`,
keep the current θ so transitions feel continuous.

**Transition recipe** (implemented in the viewer as `tweenOrbit`, port as-is to Compose):
interpolate `{θ, φ, r, target}` with easeInOutQuad over 700–900 ms; when the room is on
another floor, switch floor visibility at tween start. Deep links: `?room=Room_2krs_KPH`
animates to that room; combine with `&lights=1`, `&walls=0`, `&doors=0`, `&mode=`,
`&explode=`, `&cam=θ,φ,r`.

**Apply deep links from the model-loaded callback, never on a timer.** The room presets
and light meshes only exist once the GLB has finished parsing, and the model is now ~6 MB
— any fixed delay races the load and silently turns `?room=` into a no-op. For the same
reason an explicit room framing must outrank the viewer's deferred "fit the scene"
retarget: the viewer sets a `camLock` flag in `focusRoom` and only the *Sovita* button
clears it. Port both rules.

To "pinpoint activity" (motion, light turned on, temperature alert): look up the room or
light anchor in `cameras.json`, call your tween to its orbit, flash the room patch emissive
or toggle the fixture — everything is addressable by name.

## 6. Integration notes

* **Android:** SceneView/Filament — load GLB from assets, `getChildByName("Krs2")…isVisible`,
  Filament picking → node names. Material tweaks via `MaterialInstance` (emissive factor).
* **iOS (Kotlin/Native `platform.SceneKit.*`, SCNView via UIKitView):** SceneKit does not
  read glTF — use `marmorikatu-house.usdz`, exported by the same `hk_export` (Blender USD
  export, textures packed). Bundle it at
  `marmorikatu-mobile/composeApp/src/commonMain/composeResources/files/marmorikatu-house.usdz`.
  Guarantees, verified per export with usd-core: **Y-up stage, metersPerUnit 1**, world
  coordinates identical to the GLB/cameras.json frame; the six `Talo` children
  (Kellari/Krs1/Krs2/Terassi/Katos/Katto), every `Room_*`/`Light_*` anchor and all material
  names (`WallExt`, `Glass`, `LightOff`, …) survive verbatim — they are dot-free by design.
  USD sanitizes the **dots** in non-semantic sub-part names to `_` (`Light_1krs_KT_3.cord`
  → `Light_1krs_KT_3_cord`, `F1.wS.blk.seg0` → `F1_wS_blk_seg0`): match anchors by
  `name == anchor || name.startsWith(anchor + "_")` on iOS, `+ "."` on Android — or walk up
  to the nearest `Room_`/`Light_` ancestor from `SCNHitTestResult.node`. Visibility =
  `node.hidden`; light on/off = `SCNMaterial.emission` (§4 recipe); camera presets/tweens
  from `cameras.json` apply unchanged.
* **Multiplatform today:** ship `marmorikatu-3d.html` in a WebView (offline, ~10 MB) and
  drive it with the URL params / a small JS bridge (`focusRoom(name)`, `setLight(name,on)`,
  `setHeatByName(name,hot)`, `setHeatingVisible(v)` are global functions in the page —
  call them via `evaluateJavascript`).
* Mapping to the home-automation MCP: `list_lights` names ↔ `Light_<kerros>_<huone>` tokens;
  floors kellari/1krs/2krs match `set_lights_by_floor`.

## 7. Materials & textures

Textures are baked into the GLB/USDZ — there is nothing to do at consume time. The material
**names** the recipes above tint are the mesh material names: `WallExt`, `Glass`,
`LightOff` (§4), `HeatOff`/`HeatPipe` (§4b), and the floor set `Wood`/`Tile`/`TileDark`/
`ConcreteDark`/`ConcreteF`/`Slat`. Regenerating the procedural floor maps and the authoring
rules (UV scale, tile-module division, the 512 px normal-map budget) are a maintainer task —
[`MODELING.md`](MODELING.md) §8.

## 8. Working on the model

Everything needed to build lives in this folder; nothing depends on a cloud sandbox or a
particular session. The loop is `spec.py` → `hk_run`/`hk_export` in Blender → `python3
pack.py` → look at it.

The full maintainer guide — the build one-liner, the `BlenderB` builder API, the
drawing→coordinate calibrations, the `check_dupes.py` / `zfight.py` sweeps, and what is
verified vs still assumed — is [`MODELING.md`](MODELING.md). The native-renderer light rig
is [`LIGHTING.md`](LIGHTING.md).

**Requirements.** Blender 4.2+ (the USDZ post-process wants `usd-core`, which ships inside
Blender) and Python 3.10+ with `numpy` + `Pillow` for `mktex.py` / `pack.py`. No network
access at any point — three.js is vendored.
