# Light rig for the native Android / iPad renderers

Reference setup for the apps that consume `marmorikatu-house.glb` (Android) and
`marmorikatu-house.usdz` (iPad). It mirrors the rig in `viewer_template.html`, which was
retuned after the east facade rendered black on Android and the interiors read washed out.

**Both files share one coordinate frame.** `hk_export_usdz` exports with `forward =
NEGATIVE_Z`, verified against the glTF frame with `usd-core`, so every direction vector below
applies unchanged to the GLB and the USDZ. You do not need a mirrored rig for iPad.

---

## 1. Why the east facade goes black

The frame is the one recorded in `cameras.json`:

```
x = plan x   (north -> south, so +x is SOUTH)
y = up       (0 at the 1. krs floor)
z = -plan y  (so +z is WEST and -z is EAST)
```

A wall on the east side therefore has outward normal `(0, 0, -1)`.

The key light was aimed from `(24, 34, 16)` at `(8.5, 0, -2)`, i.e. travelling

```
d_key = normalize(target - position) = (-0.374, -0.820, -0.434)
```

The vector from a surface *to* that light is `-d_key = (0.374, 0.820, 0.434)`. Lambert term
for the east wall:

```
dot((0, 0, -1), (0.374, 0.820, 0.434)) = -0.434      negative -> no key light at all
```

West gets `+0.434` and is fine. So the east facade was lit only by ambient, and on a display
without much dynamic range that reads as black. **This is geometry, not exposure** — raising
brightness globally only washes out the three faces that were already lit.

The fix is a counter-key from `-z`. Sun position alone cannot solve it: any direction that
lights the east un-lights the west.

---

## 2. Target rig

Stated as directions and ratios, because absolute units differ per engine. Calibrate the key
to your renderer (§5), then derive the other two from it.

| role | direction of travel (normalised) | comes from | relative intensity | shadows |
|---|---|---|---|---|
| **Key (sun)** | `(-0.374, -0.820, -0.434)` | SW, 55° elevation | **1.00** | yes |
| **Fill (east counter-key)** | `(0.586, -0.506, 0.633)` | NE, 30° elevation | **0.31** | **no** |
| **Ambient / IBL** | — | sky dome | **0.16** of key | — |

Colours: key warm `#FFF1D6`, fill cool `#DFE9FF`, sky `#F4F6FF` over ground `#9A917F`.

Three rules that matter more than the exact numbers:

1. **The fill casts no shadow.** It exists to lift a facade, not to add a second shadow
   pattern. Two shadow-casting directionals give every object two conflicting shadows and
   cost a second shadow map on mobile.
2. **Ambient is the interior control, not the exterior one.** A directional is stopped by the
   walls; sky/IBL reaches everywhere. If interiors are too bright, lower ambient/IBL — do not
   lower the directionals, or the facades collapse.
3. **Ratio, not absolute.** Key:fill:ambient of `1 : 0.31 : 0.16` is what makes all four
   facades legible. The web viewer had `1 : 0.09 : 0.24`, which is what produced a black east
   side and flat interiors at the same time.

---

## 3. Android (Filament / SceneView, GLB)

Filament takes a **direction**, not a position, and directional intensity in **lux**.

```kotlin
// key -- the only shadow caster
val key = EntityManager.get().create()
LightManager.Builder(LightManager.Type.SUN)
    .color(1.0f, 0.945f, 0.839f)             // #FFF1D6
    .intensity(80_000.0f)                     // lux; calibrate per §5
    .direction(-0.374f, -0.820f, -0.434f)
    .castShadows(true)
    .sunAngularRadius(0.95f)
    .build(engine, key)

// fill -- lifts the east facade, no shadows
val fill = EntityManager.get().create()
LightManager.Builder(LightManager.Type.DIRECTIONAL)
    .color(0.875f, 0.914f, 1.0f)             // #DFE9FF
    .intensity(24_800.0f)                     // 0.31 x key
    .direction(0.586f, -0.506f, 0.633f)
    .castShadows(false)
    .build(engine, fill)

scene.addEntities(intArrayOf(key, fill))

// ambient -- interior brightness lives here
scene.indirectLight = IndirectLight.Builder()
    .reflections(iblCubemap)
    .intensity(12_800.0f)                     // 0.16 x key; drop this if rooms look washed out
    .build(engine)
```

Notes:

- Use `Type.SUN` for the key so you get the sun disc in reflections; `Type.DIRECTIONAL` for
  the fill so it contributes no specular highlight.
- Filament's shadow cascade defaults are tuned for larger scenes. The model is ~18 m across;
  set the shadow far distance to about 60 m or the depth precision is wasted.
- If you use a captured/HDRI IBL rather than a generated one, keep its **orientation**
  consistent with the sun, otherwise the IBL will fight the key and re-flatten the model.
- **If Android looks desaturated rather than dark, it is the tone mapper, not the lights.**
  Filament's default `ColorGrading` applies an ACES tone mapper, which rolls saturation out of
  bright areas — the same failure Blender's AgX default produced on this model, where pale
  timber and white joinery went flat grey no matter what the lighting did. Try:

  ```kotlin
  val cg = ColorGrading.Builder()
      .toneMapping(ColorGrading.ToneMapping.LINEAR)   // or FILMIC; ACES is the desaturating one
      .build(engine)
  view.colorGrading = cg
  ```

  Check this *before* touching intensities. Chasing it with brightness is what pushed the web
  viewer to a blown-out exposure of 1.18.

---

## 4. iPad (SceneKit, USDZ)

Same direction vectors. Two things differ from Filament and will bite if you port numbers
across:

- **SceneKit's intensity scale is ~50× smaller.** Directional `intensity` is in lux but the
  whole engine is calibrated around a default of `1000`. The key belongs at ~1600, not 80000.
  Paste Filament's figure in and you get a pure white screen.
- **SceneKit lights shine down the node's −Z axis.** There is no direction property; you
  orient the node.

```swift
let scene = try! SCNScene(url: usdzURL, options: nil)
sceneView.autoenablesDefaultLighting = false     // or SceneKit adds an omni that flattens everything
sceneView.scene = scene

func directional(_ dir: SIMD3<Float>, lux: CGFloat, color: UIColor, shadows: Bool) -> SCNNode {
    let light = SCNLight()
    light.type = .directional
    light.color = color
    light.intensity = lux
    light.castsShadow = shadows
    if shadows {
        light.shadowMode = .deferred
        light.shadowMapSize = CGSize(width: 2048, height: 2048)
        light.shadowSampleCount = 16
        light.shadowRadius = 3
        light.shadowColor = UIColor(white: 0, alpha: 0.45)
        light.orthographicScale = 14          // half-extent in m; must cover an ~18 m model
        light.zNear = 1; light.zFar = 120
        light.shadowBias = 0.004
    }
    let node = SCNNode()
    node.light = light
    node.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, -1), to: normalize(dir))
    return node
}

let key  = directional(SIMD3(-0.374, -0.820, -0.434), lux: 1600,
                       color: UIColor(red: 1.0, green: 0.945, blue: 0.839, alpha: 1), shadows: true)
let fill = directional(SIMD3( 0.586, -0.506,  0.633), lux: 500,      // 0.31 x key
                       color: UIColor(red: 0.875, green: 0.914, blue: 1.0, alpha: 1), shadows: false)
scene.rootNode.addChildNode(key)
scene.rootNode.addChildNode(fill)

// IBL. The USDZ's materials are physicallyBased, and PBR in SceneKit looks dead without an
// environment -- this is also your interior-brightness control.
scene.lightingEnvironment.contents = UIImage(named: "sky_soft")   // equirect or cube
scene.lightingEnvironment.intensity = 0.26                        // lower if rooms wash out
scene.background.contents = UIColor(red: 0.82, green: 0.86, blue: 0.91, alpha: 1)

let cam = SCNCamera()
cam.wantsHDR = true
cam.wantsExposureAdaptation = false   // see below
cam.exposureOffset = 0
cam.bloomIntensity = 0
cam.zNear = 0.1; cam.zFar = 300
```

Four SceneKit-specific traps, roughly in the order people hit them:

1. **`wantsExposureAdaptation` defaults to true once `wantsHDR` is on.** SceneKit then
   auto-exposes per frame, so brightness drifts as you orbit and every fixed light value you
   choose gets undone. If the iPad "looks fine sometimes", this is why. Turn it off before
   tuning anything.
2. **`autoenablesDefaultLighting` must be off.** It adds an omni light at the camera, which
   removes exactly the facade-to-facade contrast this rig is built to create.
3. **`lightingEnvironment` is not optional.** Without it the imported PBR materials render
   dark and flat, and the natural reaction — raising the directionals — produces a harsh,
   blown key with still-dead shadow sides.
4. **Linear-space rendering must stay on.** Check `SCNDisableLinearSpaceRendering` is not set
   to `true` in `Info.plist`; with it on, the colours will not match Android at any intensity.

`scene.background.contents` and `scene.lightingEnvironment.contents` are separate — set both,
or you get a correctly lit model floating on the default grey.

---

## 5. Calibrating the key, then verifying

Absolute lux differs per engine and per tone mapper — Filament wants ~80000 for the key where
SceneKit wants ~1600 for the same picture — so **do not port intensities between the two
apps**. Port the ratios. Set the key by eye once per engine and derive the rest arithmetically:

1. Point the camera at the **west** facade (the sunlit one) in the middle of the day.
2. Raise the key until the pale timber sits just below clipping — bright but still showing the
   board shadow lines. Do not use the white joinery to judge; it clips first and will mislead
   you, which is how the web viewer ended up over-bright at exposure 1.18.
3. Set fill `= 0.31 x key` and ambient `= 0.16 x key`.
4. Then adjust **ambient only** until the interiors look right. Leave the directionals alone.

Four checks before you call it done:

- **East facade** (`-z`): clearly readable, roughly a third as bright as the west. If it is
  black, the fill direction has the wrong sign on `z`.
- **Interiors**: rooms darker than the sunlit facade, with visible falloff away from windows.
  Flat, evenly-lit rooms mean ambient is too high.
- **One shadow per object**, not two. Two means the fill is still casting.
- **North gable** (`-x`): should be the darkest elevation, lit by ambient plus a little fill.
  It is the honest test of whether ambient is doing its job.

---

## 6. Values currently in the web viewer

For reference — `viewer_template.html`, in three.js units:

| | value |
|---|---|
| tone mapping | `ACESFilmic`, exposure `1.06` |
| key | `DirectionalLight(0xfff1d6, 1.62)` at `(24, 34, 16)` → `(8.5, 0, -2)`, shadows on |
| fill | `DirectionalLight(0xdfe9ff, 0.50)` at `(-10, 16, -22)` → `(8.5, 0, -2)`, shadows off |
| hemisphere | `(0xf4f6ff, 0x9a917f, 0.26)` |
| environment | procedural PMREM sky, `envMapIntensity` 0.45 (glass 1.1, wood 0.20) |

The history is worth keeping in mind when tuning: the sun went `1.28 → 2.05 → 1.62` and the
exposure `0.98 → 1.18 → 1.06`. Both overshot on the way up because brightness was being used
to fix what was actually a missing fill light.
