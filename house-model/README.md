# Marmorikatu 10 — interactive 3D house model

Built from the architect drawings (`0krs/1krs/2krs_pohja50` DWG-vector extraction, `julkisivut`
elevations), the electrical drawings (`1/2 krs valaistus` — light positions), and the owner's
photos. Levels +132.86 / +135.90 / +138.91, ridge +143.60.

## Files (house-model/)

| file | purpose |
|---|---|
| `marmorikatu.blend` | Blender scene — rebuild after editing `spec.py` (snippet below) |
| `marmorikatu-house.glb` | The model (~1.8 MB, textured, y-up glTF) — Android/web |
| `marmorikatu-house.usdz` | Same model for iOS/SceneKit (USD, Y-up, names preserved) |
| `viewer.html` | three.js reference viewer (fetches the .glb next to it) |
| `marmorikatu-3d.html` | Same viewer fully self-contained (offline / WebView-ready) |
| `cameras.json` | Generated per-room camera presets + light anchor positions |
| `spec.py` / `bpy_backend.py` | Parametric source of truth |
| `tex/*.jpg` | PBR textures (Poly Haven CC0, palette-matched: siding, floor, pavers, concrete, brick, lawn) + normal maps |

Rebuild + re-export in Blender's Python console:

```python
ns={}; BASE='/Users/kyostiherrala/IdeaProjects/marmorikatu-home-automation/house-model'
exec(compile(open(BASE+'/bpy_backend.py').read(),'b','exec'),ns); ns['hk_run'](BASE); ns['hk_export'](BASE)
```

---

# Technical contract for the mobile app (Kotlin Multiplatform / Compose)

## 1. Coordinate system

glTF/three.js: **x** runs pohjoinen→etelä along the house (0…16.98), **y** is up with
0 = 1. krs floor (+135.90; kellari −3.04, 2. krs +3.01), **z** = −(plan west→east), i.e.
the terrace side is +z, the itä facade −z. All distances in meters. Yard levels per the
asemapiirustus: entrance yard/carport bay −0.55 (+135.35), street corner −0.78 (+135.10),
VAR floor −0.05 (+135.85), SW terrace yard −3.00 (+132.90).

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
│   └── <G>_valot         Light_* fixtures (see §4)
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
be focused and its lights (`Light_1krs_KT_*`, `Light_1krs_RUOKAILU`, `Light_1krs_OH_*`) controlled
independently.
Highlight recipe used in the viewer: clone material, set `emissive=#2563eb`,
`emissiveIntensity≈0.35`. The `<kerros>` token (kellari/1krs/2krs/katos) matches the
home-automation floor naming, so `set_lights_by_floor` / room states map 1:1.

## 4. Lights (from the electrical drawings)

Each fixture is an individually named mesh — the on/off **anchor**:

```
Light_<kerros>_<huone>[_n]     interior     e.g. Light_1krs_KT_1, Light_2krs_AULA_2
Light_ulko_*                   outdoors     etuovi_1/2, tekn, terassi_1/2, parveke,
                                            katos, piha_1..3 (bollards by the terrace)
Light_katos_*                  carport      katos_1/2, katos_VAR
```

49 anchors total; positions per the valaistus drawings (ceiling points, spots, island +
dining pendants, wall sconces, yard bollards). Sub-parts (cords, poles) carry a `.` suffix —
the anchor is always the dot-free name. `cameras.json → lights` lists every anchor's world
position for placing tap targets or badges without traversing the scene.

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
* **Multiplatform today:** ship `marmorikatu-3d.html` in a WebView (offline, ~2.4 MB) and
  drive it with the URL params / a small JS bridge (`focusRoom(name)`, `setLight(name,on)`
  are global functions in the page — call them via `evaluateJavascript`).
* Mapping to the home-automation MCP: `list_lights` names ↔ `Light_<kerros>_<huone>` tokens;
  floors kellari/1krs/2krs match `set_lights_by_floor`.
