"""Find geometry that is modelled twice: pairs of boxes of the same material whose
bounding volumes overlap by more than half of the smaller one. Two builders adding the
same facade panel from different passes is the classic case -- the boxes never coincide
exactly (each pass rounds differently), so an exact-extent test misses them."""
import sys, os, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import spec.py next to us
import importlib, spec
importlib.reload(spec)

class CB:
    def __init__(self): self.items = []; self.floor = None; self.zoff = 0.0
    def box(self, name, xs, ys, zs, mat):
        self.items.append((name, self.floor, min(xs), max(xs), min(ys), max(ys),
                           zs[0] + self.zoff, zs[1] + self.zoff, mat))
    def cyl(self, name, x, y, z0, z1, r, mat, segs=20):
        self.items.append((name, self.floor, x - r, x + r, y - r, y + r,
                           z0 + self.zoff, z1 + self.zoff, mat))
    def sph(self, name, x, y, z, r, mat):
        self.items.append((name, self.floor, x - r, x + r, y - r, y + r,
                           z - r + self.zoff, z + r + self.zoff, mat))
    # Non-box primitives are structure, never facade panels, so this pass can ignore
    # them outright -- but every builder method spec.py calls must exist here or
    # build_all dies on the first one it meets and the check silently stops covering
    # everything below it.
    def slab(self, *a, **k): pass
    def room(self, *a, **k): pass
    def roofquad(self, *a, **k): pass
    def prism(self, *a, **k): pass
    def tube(self, *a, **k): pass
    def polyseg(self, *a, **k): pass

B = CB(); spec.build_all(B)
print(f'{len(B.items)} box/cyl primitives collected')

MATS = sys.argv[1].split(',') if len(sys.argv) > 1 else None
items = [it for it in B.items if MATS is None or it[8] in MATS]
print(f'{len(items)} of material {MATS or "ANY"}')

def vol(a): return (a[3]-a[2]) * (a[5]-a[4]) * (a[7]-a[6])

hits = []
for a, b in itertools.combinations(items, 2):
    if a[8] != b[8]: continue
    dx = min(a[3], b[3]) - max(a[2], b[2])
    dy = min(a[5], b[5]) - max(a[4], b[4])
    dz = min(a[7], b[7]) - max(a[6], b[6])
    if dx <= 0 or dy <= 0 or dz <= 0: continue
    ov = dx * dy * dz
    frac = ov / max(1e-9, min(vol(a), vol(b)))
    if frac > 0.5:
        hits.append((frac, a, b))

hits.sort(key=lambda h: -h[0])
print(f'\n== {len(hits)} pair(s) overlapping >50% of the smaller box ==')
for frac, a, b in hits:
    print(f'  {frac*100:5.1f}%  {a[0]:20s} [{a[1]}] x {a[2]:7.3f}..{a[3]:7.3f} '
          f'y {a[4]:7.3f}..{a[5]:7.3f} z {a[6]:7.3f}..{a[7]:7.3f}')
    print(f'          {b[0]:20s} [{b[1]}] x {b[2]:7.3f}..{b[3]:7.3f} '
          f'y {b[4]:7.3f}..{b[5]:7.3f} z {b[6]:7.3f}..{b[7]:7.3f}  [{a[8]}]')
