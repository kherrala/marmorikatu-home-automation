#!/usr/bin/env python3
"""Package the exported GLB into the self-contained viewer + sanity-check the GLB.

Runs anywhere: every path is derived from this file's own directory, so the same
script works in the cloud sandbox and on the Mac
(.../marmorikatu-home-automation/house-model/pack.py).

    python3 pack.py [path/to/marmorikatu-house.glb] [--max-mib N]

Outputs:
  out/marmorikatu-house.glb   full-quality copy of the export -- this is the Android asset
  out/marmorikatu-house.usdz  copied alongside when the export produced one (iOS)
  out/marmorikatu-3d.html     self-contained viewer with the GLB embedded as base64
  out/cameras.json            per-room presets, light/heating anchors

out/ is the staging area; the two files git actually tracks live at the house-model root
(marmorikatu-3d.html, cameras.json -- the .glb/.usdz/.blend are written there by
hk_export itself), so both are copied up at the end. Run pack.py and the repo is current;
no manual copying step to forget.

--max-mib caps the finished HTML. Base64 inflates the payload by 4/3, so a 8.6 MB GLB
lands at ~12 MiB of HTML; if a delivery channel has a ceiling (the Cowork artifact
gallery caps at 10 MiB, for one) pass e.g. --max-mib 10 and the viewer copy walks down
a texture-quality ladder until it fits. Normal maps go first: they are the bulk of the
texture budget and the least missed at half resolution. Geometry is never touched, and
out/marmorikatu-house.glb always stays full quality, so cameras.json, anchor names and
room bounds are identical either way. Without the flag nothing is re-encoded.
"""
import base64, json, struct, sys, os, io, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(BASE, 'out')
argv = sys.argv[1:]
HTML_LIMIT = None
if '--max-mib' in argv:
    i = argv.index('--max-mib')
    HTML_LIMIT = int(round(float(argv[i + 1]) * 1024 * 1024)) - 300 * 1024
    del argv[i:i + 2]
GLB = argv[0] if argv else os.path.join(BASE, 'marmorikatu-house.glb')
os.makedirs(OUT, exist_ok=True)

raw = open(GLB, 'rb').read()
magic, ver, total = struct.unpack('<III', raw[:12])
assert magic == 0x46546C67, 'not a GLB'
jlen, jtype = struct.unpack('<II', raw[12:20])
gltf = json.loads(raw[20:20 + jlen])
blen, btype = struct.unpack('<II', raw[20 + jlen:28 + jlen])
BIN = raw[28 + jlen:28 + jlen + blen]

names  = [n.get('name', '?') for n in gltf['nodes']]
top    = [names[i] for i in gltf['scenes'][gltf.get('scene', 0)]['nodes']]
floors = [n for n in names if n in ('Talo', 'Kellari', 'Krs1', 'Krs2', 'Katto', 'Terassi')]
rooms  = [n for n in names if n.startswith('Room_')]
meshes = len(gltf.get('meshes', []))
mats   = [m.get('name') for m in gltf.get('materials', [])]
print(f'GLB {len(raw)/1e6:.2f} MB, nodes={len(names)} meshes={meshes} mats={len(mats)}')
print('top nodes:', top)
print('floor groups found:', floors)
print(f'room patches: {len(rooms)}:', sorted(rooms))
missing = [f for f in ('Talo', 'Kellari', 'Krs1', 'Krs2', 'Katto', 'Terassi') if f not in names]
print('MISSING GROUPS:', missing if missing else 'none')

# ---------------------------------------------------------------- texture budget
def b64len(n):            # exact base64 length without doing the encode
    return ((n + 2) // 3) * 4

def rebuild(gltf_in, view_bytes):
    """Serialise a GLB from a glTF dict plus {bufferView index: bytes}.

    bufferView byteOffsets are rewritten in index order, each 4-byte aligned, so
    accessors (whose own byteOffset is relative to the view) stay valid.
    """
    g = json.loads(json.dumps(gltf_in))          # deep copy; we mutate offsets
    blob = bytearray()
    for i, v in enumerate(g['bufferViews']):
        while len(blob) % 4:
            blob.append(0)
        d = view_bytes[i]
        v['byteOffset'] = len(blob)
        v['byteLength'] = len(d)
        blob += d
    while len(blob) % 4:
        blob.append(0)
    g['buffers'][0]['byteLength'] = len(blob)
    g['buffers'][0].pop('uri', None)
    js = json.dumps(g, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((4 - len(js) % 4) % 4)
    head = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    return (head + struct.pack('<II', len(js), 0x4E4F534A) + js
                 + struct.pack('<II', len(blob), 0x004E4942) + bytes(blob))

def orig_views():
    out = {}
    for i, v in enumerate(gltf['bufferViews']):
        o = v.get('byteOffset', 0)
        out[i] = BIN[o:o + v['byteLength']]
    return out

def reduced(nor_px, nor_q, dif_px, dif_q, sub):
    """Re-encode every image bufferView; normals and diffuse get separate budgets."""
    from PIL import Image
    views = orig_views()
    for im in gltf.get('images', []):
        bv = im.get('bufferView')
        if bv is None:
            continue
        nm = im.get('name', '')
        px, q = (nor_px, nor_q) if nm.endswith('_nor') else (dif_px, dif_q)
        try:
            img = Image.open(io.BytesIO(views[bv]))
            img.load()
        except Exception:
            continue                                   # unreadable: leave it alone
        if max(img.size) > px:
            img = img.resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=q, subsampling=sub)
        views[bv] = buf.getvalue()
        im['mimeType'] = 'image/jpeg'
    return views

tpl   = open(os.path.join(BASE, 'viewer_template.html')).read()
tag   = '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
# three.js is inlined so the viewer works fully offline (WebView-friendly). Vendored
# copy first -- the cloud sandbox unpacked it under package/, the repo keeps vendor/.
cands = [os.path.join(BASE, 'vendor', 'three.min.js'),
         os.path.join(BASE, 'package', 'build', 'three.min.js')]
three = next((open(p).read() for p in cands if os.path.exists(p)), None)
if three is None:
    sys.exit('three.min.js not found in ' + ' or '.join(cands))
wrapper = len(tpl) - len(tag) + len(three)             # everything except the base64

# quality ladder, gentlest first; None = embed the export untouched
LADDER = [None,
          (512, 90, 1024, 92, 0),
          (512, 84, 1024, 88, 0),
          (512, 80,  768, 86, 2),
          (384, 76,  512, 82, 2)]
glb, label = raw, 'full quality'
for step in LADDER if HTML_LIMIT else []:
    if step is not None:
        glb = rebuild(gltf, reduced(*step))
        label = f'normals<={step[0]}px q{step[1]}, diffuse<={step[2]}px q{step[3]}'
    if wrapper + b64len(len(glb)) <= HTML_LIMIT:
        break
    print(f'  viewer would be {(wrapper+b64len(len(glb)))/2**20:.2f} MiB with {label} -- reducing')
print(f'viewer GLB: {len(glb)/1e6:.2f} MB ({label})')

emb = tpl.replace(tag, '<script>\n' + three + '\n</script>').replace(
    '__GLB_BASE64__', base64.b64encode(glb).decode())
open(os.path.join(OUT, 'marmorikatu-3d.html'), 'w').write(emb)
shutil.copy(GLB, os.path.join(OUT, 'marmorikatu-house.glb'))   # Android gets full quality
for extra in ('marmorikatu-house.usdz',):
    src = os.path.join(os.path.dirname(os.path.abspath(GLB)), extra)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(OUT, extra))

# ---- cameras.json: per-room presets + light anchors (three.js y-up coords) ----
def node_mesh_bbox(g, node):
    mi = node.get('mesh')
    if mi is None: return None
    lo = [1e9]*3; hi = [-1e9]*3
    for prim in g['meshes'][mi]['primitives']:
        acc = g['accessors'][prim['attributes']['POSITION']]
        if 'min' in acc and 'max' in acc:
            lo = [min(a, b) for a, b in zip(lo, acc['min'])]
            hi = [max(a, b) for a, b in zip(hi, acc['max'])]
    return None if lo[0] > hi[0] else (lo, hi)

rooms = {}; lights = {}
for n in gltf['nodes']:
    nm = n.get('name', '')
    bb = node_mesh_bbox(gltf, n)
    if not bb: continue
    lo, hi = bb
    c = [(a+b)/2 for a, b in zip(lo, hi)]; s = [b-a for a, b in zip(lo, hi)]
    if nm.startswith('Room_'):
        rooms[nm] = {'center': [round(v, 3) for v in c],
                     'size':   [round(v, 3) for v in s],
                     'orbit':  {'target': [round(c[0], 2), round(c[1]+1.1, 2), round(c[2], 2)],
                                'radius': round(max(3.5, max(s[0], s[2])*2.1), 2), 'phi': 0.55}}
    elif nm.startswith('Light_') and '.' not in nm:
        lights[nm] = {'position': [round(v, 3) for v in c]}

# ---- floor-heating circuits: Heat_<kerros>_<nn> overlay patches, metadata from spec.HEAT ----
sys.path.insert(0, BASE)
try:
    from spec import HEAT as HEATSPEC
except Exception:
    HEATSPEC = {}
heating = {}; manifolds = {}
for n in gltf['nodes']:
    nm = n.get('name', '')
    if not nm.startswith('Heat_') or '.' in nm: continue    # anchors only ('.pipe' is a sub-part)
    bb = node_mesh_bbox(gltf, n)
    if not bb: continue
    lo, hi = bb
    c = [(a+b)/2 for a, b in zip(lo, hi)]; s = [b-a for a, b in zip(lo, hi)]
    nn = nm.split('_')[-1]
    if not nn.isdigit():
        manifolds[nm] = {'center': [round(v, 3) for v in c]}   # Heat_<kerros>_JTn
        continue
    info = HEATSPEC.get(nn)
    heating[nm] = {'circuit': nn, 'floor': nm.split('_')[1],
                   'center': [round(v, 3) for v in c], 'size': [round(v, 3) for v in s],
                   'rooms': info[1] if info else '', 'loop_m': info[2] if info else None}

floorsets = {'kellari': {'groups': ['Kellari'], 'mode': 'kellari'},
             'krs1':    {'groups': ['Krs1', 'Terassi', 'Katos'], 'mode': 'krs1'},
             'krs2':    {'groups': ['Krs2'], 'mode': 'krs2'},
             'all':     {'groups': ['Kellari', 'Krs1', 'Krs2', 'Terassi', 'Katto', 'Katos'], 'mode': 'all'}}
cams = {'coordinate_system': 'three.js / glTF: x=plan-x (pohjoinen->etela), y=up (0 = 1krs floor), z=-plan-y (lansi->ita negative)',
        'floors': floorsets, 'rooms': rooms, 'lights': lights,
        'heating': heating, 'manifolds': manifolds,
        'tween': 'easeInOutQuad over 700-900 ms on {target,radius,phi,theta} - see viewer source'}
json.dump(cams, open(os.path.join(OUT, 'cameras.json'), 'w'), indent=1, ensure_ascii=False)
print(f'cameras.json: {len(rooms)} rooms, {len(lights)} lights, {len(heating)} heating circuits')
print(f'wrote {OUT}/marmorikatu-3d.html ({len(emb)/2**20:.2f} MiB), glb + usdz copies')

# the repo tracks these two at the house-model root, not under out/
for f in ('marmorikatu-3d.html', 'cameras.json'):
    if os.path.abspath(OUT) != os.path.abspath(BASE):
        shutil.copy(os.path.join(OUT, f), os.path.join(BASE, f))
print('refreshed repo-root copies: marmorikatu-3d.html, cameras.json')

# ---- z-fighting check.  Runs off spec.py, not the GLB, so it needs no Blender
# and costs a few seconds.  Warns only: packing has already succeeded by here and
# a coplanar face is a cosmetic fault, not a broken export.  See zfight.py.
# It walks every coplanar face pair, so it takes ~a minute on this model.  ZFIGHT=0 skips it
# when you are iterating and only want the export.
try:
    if os.environ.get('ZFIGHT') == '0':
        raise RuntimeError('skipped via ZFIGHT=0')
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(BASE, 'zfight.py')],
                       capture_output=True, text=True, timeout=900)
    tail = [l for l in r.stdout.strip().splitlines() if l.startswith('  NEW') or l.startswith('new:')]
    if r.returncode:
        print('\n*** NEW coplanar faces since the baseline — these will z-fight in the viewer:')
        for l in tail: print(l)
        print('*** run `python3 zfight.py` for the full list, or `--baseline` to accept them')
    else:
        print('zfight: no new coplanar faces')
except Exception as e:
    print(f'zfight: skipped ({e})')
