#!/usr/bin/env python3
"""Package the exported GLB into the self-contained viewer + sanity-check the GLB."""
import base64, json, struct, sys, os

GLB = sys.argv[1] if len(sys.argv)>1 else '/home/claude/house/marmorikatu-house.glb'
OUT = '/home/claude/house/out'
os.makedirs(OUT, exist_ok=True)

raw = open(GLB,'rb').read()
magic, ver, total = struct.unpack('<III', raw[:12])
assert magic == 0x46546C67, 'not a GLB'
jlen, jtype = struct.unpack('<II', raw[12:20])
gltf = json.loads(raw[20:20+jlen])

names = [n.get('name','?') for n in gltf['nodes']]
top = [names[i] for i in gltf['scenes'][gltf.get('scene',0)]['nodes']]
floors = [n for n in names if n in ('Talo','Kellari','Krs1','Krs2','Katto','Terassi')]
rooms  = [n for n in names if n.startswith('Room_')]
meshes = len(gltf.get('meshes',[]))
mats   = [m.get('name') for m in gltf.get('materials',[])]
print(f'GLB {len(raw)/1e6:.2f} MB, nodes={len(names)} meshes={meshes} mats={len(mats)}')
print('top nodes:', top)
print('floor groups found:', floors)
print(f'room patches: {len(rooms)}:', sorted(rooms))
missing = [f for f in ('Talo','Kellari','Krs1','Krs2','Katto','Terassi') if f not in names]
print('MISSING GROUPS:', missing if missing else 'none')

b64 = base64.b64encode(raw).decode()
tpl = open('/home/claude/house/viewer_template.html').read()
# inline three.js so the viewer works fully offline (WebView-friendly)
three = open('/home/claude/house/package/build/three.min.js').read()
tag = '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
tpl = tpl.replace(tag, '<script>\n'+three+'\n</script>')
emb = tpl.replace('__GLB_BASE64__', b64)
open(f'{OUT}/marmorikatu-3d.html','w').write(emb)
import shutil; shutil.copy(GLB, f'{OUT}/marmorikatu-house.glb')

# ---- cameras.json: per-room presets + light anchors (three.js y-up coords) ----
def node_mesh_bbox(g, node):
    mi = node.get('mesh')
    if mi is None: return None
    lo=[1e9]*3; hi=[-1e9]*3
    for prim in g['meshes'][mi]['primitives']:
        acc=g['accessors'][prim['attributes']['POSITION']]
        if 'min' in acc and 'max' in acc:
            lo=[min(a,b) for a,b in zip(lo,acc['min'])]; hi=[max(a,b) for a,b in zip(hi,acc['max'])]
    return None if lo[0]>hi[0] else (lo,hi)

rooms={}; lights={}
for n in gltf['nodes']:
    nm=n.get('name','')
    bb=node_mesh_bbox(gltf,n)
    if not bb: continue
    lo,hi=bb; c=[(a+b)/2 for a,b in zip(lo,hi)]; s=[b-a for a,b in zip(lo,hi)]
    if nm.startswith('Room_'):
        rooms[nm]={'center':[round(v,3) for v in c],
                   'size':[round(v,3) for v in s],
                   'orbit':{'target':[round(c[0],2),round(c[1]+1.1,2),round(c[2],2)],
                            'radius':round(max(3.5,max(s[0],s[2])*2.1),2),'phi':0.55}}
    elif nm.startswith('Light_') and '.' not in nm:
        lights[nm]={'position':[round(v,3) for v in c]}
# ---- floor-heating circuits: Heat_<kerros>_<nn> overlay patches, metadata from spec.HEAT ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from spec import HEAT as HEATSPEC
except Exception:
    HEATSPEC={}
heating={}
for n in gltf['nodes']:
    nm=n.get('name','')
    if not nm.startswith('Heat_'): continue
    bb=node_mesh_bbox(gltf,n)
    if not bb: continue
    lo,hi=bb; c=[(a+b)/2 for a,b in zip(lo,hi)]; s=[b-a for a,b in zip(lo,hi)]
    nn=nm.split('_')[-1]; info=HEATSPEC.get(nn)
    heating[nm]={'circuit':nn,'floor':nm.split('_')[1],
                 'center':[round(v,3) for v in c],'size':[round(v,3) for v in s],
                 'rooms':info[1] if info else '','loop_m':info[2] if info else None}

floors={'kellari':{'groups':['Kellari'],'mode':'kellari'},
        'krs1':{'groups':['Krs1','Terassi','Katos'],'mode':'krs1'},
        'krs2':{'groups':['Krs2'],'mode':'krs2'},
        'all':{'groups':['Kellari','Krs1','Krs2','Terassi','Katto','Katos'],'mode':'all'}}
cams={'coordinate_system':'three.js / glTF: x=plan-x (pohjoinen->etela), y=up (0 = 1krs floor), z=-plan-y (lansi->ita negative)',
      'floors':floors,'rooms':rooms,'lights':lights,'heating':heating,
      'tween':'easeInOutQuad over 700-900 ms on {target,radius,phi,theta} — see viewer source'}
json.dump(cams, open(f'{OUT}/cameras.json','w'), indent=1, ensure_ascii=False)
print(f'cameras.json: {len(rooms)} rooms, {len(lights)} lights, {len(heating)} heating circuits')
print(f'wrote {OUT}/marmorikatu-3d.html ({len(emb)/1e6:.2f} MB), glb copy')
