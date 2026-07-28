#!/usr/bin/env python3
"""Coplanar-face (z-fighting) check for the Marmorikatu spec.

Replays spec.py against a recording builder and looks for pairs of faces that
lie in the same plane, overlap in that plane, and FACE THE SAME WAY.  That last
condition is the whole point:

  * two faces back to back  (a wall's underside on a slab's top surface) is a
    butt joint.  They are coincident but you only ever see one of them, because
    the other is inside the solid it belongs to.  Not a defect.
  * two faces pointing the same way, both exposed, is a z-fight.  The renderer
    has no way to order them and they flicker.  That is what this reports.

An earlier bounding-box version of this check was useless -- it flagged 177
"hits" of which essentially all were false: L-shaped room slabs whose boxes
overlap but whose outlines do not, roof ribs sharing the roof's y extent,
walls meeting at internal corners.  Boxes cannot tell you any of this; you have
to compare the actual face polygons.

Runs without Blender: the builder API is small and every solid in the spec is
either a box or an extruded polygon, so the geometry can be reproduced exactly
in plain Python.

    python3 zfight.py [min_area_m2]        # default 0.02, exit 1 if any found
"""
import sys, math, itertools

_args = [a for a in sys.argv[1:] if not a.startswith('-')]
MIN_AREA = float(_args[0]) if _args else 0.02
EPS_N, EPS_D = 1e-4, 1e-4          # normal / plane-offset match tolerance


# ---------------------------------------------------------------- geometry --
def _sub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def _cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _dot(a, b): return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]


def _normal(poly):
    """Newell's method — robust for non-planar-ish and concave polygons."""
    n = [0.0, 0.0, 0.0]
    for i, p in enumerate(poly):
        q = poly[(i+1) % len(poly)]
        n[0] += (p[1]-q[1])*(p[2]+q[2])
        n[1] += (p[2]-q[2])*(p[0]+q[0])
        n[2] += (p[0]-q[0])*(p[1]+q[1])
    L = math.sqrt(sum(c*c for c in n))
    return None if L < 1e-12 else (n[0]/L, n[1]/L, n[2]/L)


def _project(poly, n):
    """Drop the dominant axis of n to get a 2D polygon, keeping orientation."""
    ax = max(range(3), key=lambda i: abs(n[i]))
    if ax == 0:   return [(p[1], p[2]) for p in poly]
    elif ax == 1: return [(p[2], p[0]) for p in poly]
    else:         return [(p[0], p[1]) for p in poly]


def _area2(poly):
    s = 0.0
    for i, p in enumerate(poly):
        q = poly[(i+1) % len(poly)]
        s += p[0]*q[1] - q[0]*p[1]
    return s/2.0


def _earclip(poly):
    """Triangulate a simple polygon (convex or concave).  Good enough here:
    every outline in the spec is a small rectilinear shape."""
    pts = list(poly)
    if _area2(pts) < 0: pts.reverse()
    tris, guard = [], 0
    while len(pts) > 3 and guard < 5000:
        guard += 1
        for i in range(len(pts)):
            a, b, c = pts[i-2], pts[i-1], pts[i]
            if (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]) <= 0:
                continue                                   # reflex
            if any(_inside_tri(p, a, b, c) for p in pts if p not in (a, b, c)):
                continue                                   # not an ear
            tris.append((a, b, c)); pts.pop(i-1); break
        else:
            break
    if len(pts) == 3: tris.append(tuple(pts))
    return tris


def _inside_tri(p, a, b, c):
    d1 = (p[0]-b[0])*(a[1]-b[1]) - (a[0]-b[0])*(p[1]-b[1])
    d2 = (p[0]-c[0])*(b[1]-c[1]) - (b[0]-c[0])*(p[1]-c[1])
    d3 = (p[0]-a[0])*(c[1]-a[1]) - (c[0]-a[0])*(p[1]-a[1])
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def _clip(subject, clipper):
    """Sutherland-Hodgman.  Both must be convex; we only ever pass triangles."""
    out = list(subject)
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i+1) % len(clipper)]
        inp, out = out, []
        if not inp: return []
        side = lambda p: (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])
        for j, p in enumerate(inp):
            q = inp[(j+1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= 0: out.append(p)
            if (sp > 0) != (sq > 0):
                t = sp/(sp-sq) if abs(sp-sq) > 1e-15 else 0.0
                out.append((p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1])))
    return out


def overlap_area(p1, p2):
    total = 0.0
    for t1 in _earclip(p1):
        if _area2(t1) < 0: t1 = t1[::-1]
        for t2 in _earclip(p2):
            if _area2(t2) < 0: t2 = t2[::-1]
            c = _clip(t1, t2)
            if len(c) >= 3: total += abs(_area2(c))
    return total


# ------------------------------------------------------------ the recorder --
class Recorder:
    """Same surface as BlenderB, but records face polygons instead of meshes."""

    def __init__(self):
        self.floor = None; self.zoff = 0.0; self.base = None
        self.solids = []          # (name, [face, ...]) with face = [(x,y,z), ...]

    # -- solids -------------------------------------------------------------
    def box(self, name, xs, ys, zs, mat):
        z0, z1 = zs[0]+self.zoff, zs[1]+self.zoff
        x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
        if x1-x0 < 1e-4 or y1-y0 < 1e-4 or z1-z0 < 1e-4: return
        f = [
            [(x0,y0,z0),(x0,y1,z0),(x1,y1,z0),(x1,y0,z0)],   # -z
            [(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)],   # +z
            [(x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1)],   # -y
            [(x0,y1,z0),(x0,y1,z1),(x1,y1,z1),(x1,y1,z0)],   # +y
            [(x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0)],   # -x
            [(x1,y0,z0),(x1,y1,z0),(x1,y1,z1),(x1,y0,z1)],   # +x
        ]
        self.solids.append((name, f))

    def _prism(self, name, pts, vec, mat):
        if len(pts) < 3: return
        top = [(p[0]+vec[0], p[1]+vec[1], p[2]+vec[2]) for p in pts]
        faces = [list(pts), top[::-1]]
        for i in range(len(pts)):
            j = (i+1) % len(pts)
            faces.append([pts[i], pts[j], top[j], top[i]])
        self.solids.append((name, faces))

    def slab(self, name, poly, z0, z1, mat, holes=None):
        self._prism(name, [(p[0], p[1], z0+self.zoff) for p in poly], (0, 0, z1-z0), mat)

    def room(self, name, poly, mat, z=0.0, holes=None):
        self._prism(name, [(p[0], p[1], z+0.012) for p in poly], (0, 0, 0.008), mat)

    def roofquad(self, name, pts, thick, mat):
        self._prism(name, [(p[0], p[1], p[2]) for p in pts], (0, 0, -thick), mat)

    def prism(self, name, x0, x1, poly_yz, mat, axis='x'):
        if axis == 'x': self._prism(name, [(x0, a, b) for (a, b) in poly_yz], (x1-x0, 0, 0), mat)
        else:           self._prism(name, [(a, x0, b) for (a, b) in poly_yz], (0, x1-x0, 0), mat)

    # -- curved solids: skipped.  Tessellated cylinders and spheres do not
    #    produce large coplanar faces, and including them would swamp the
    #    report with 20-segment facet noise.
    def cyl(self, *a, **k): pass
    def sph(self, *a, **k): pass
    def tube3(self, *a, **k): pass

    def __getattr__(self, k):                      # lights, heating, anything else
        return lambda *a, **kw: None


# --------------------------------------------------------------- the check --
def faces_with_outward_normals(name, faces):
    """Yield (normal, offset, polygon) with the normal pointing out of the solid.

    Orientation is decided by where the rest of the solid's vertices sit.  If
    they straddle the plane the face is on a concave part of the outline and the
    outward direction is genuinely ambiguous, so it is skipped rather than
    guessed at."""
    allpts = [p for f in faces for p in f]
    for f in faces:
        n = _normal(f)
        if n is None: continue
        d = _dot(n, f[0])
        side = [_dot(n, p)-d for p in allpts]
        if max(side) > 1e-6 and min(side) < -1e-6:
            continue
        if max(side) > 1e-6:
            n = (-n[0], -n[1], -n[2]); d = -d
        yield n, d, f


def main():
    sys.path.insert(0, __file__.rsplit('/', 1)[0] or '.')
    import spec
    B = Recorder()
    spec.build_all(B)

    planes = {}
    for name, faces in B.solids:
        for n, d, poly in faces_with_outward_normals(name, faces):
            key = (round(n[0], 4)+0.0, round(n[1], 4)+0.0, round(n[2], 4)+0.0, round(d, 4)+0.0)
            planes.setdefault(key, []).append((name, n, poly))

    hits = []
    for key, group in planes.items():
        if len(group) < 2: continue
        for (na, n1, p1), (nb, n2, p2) in itertools.combinations(group, 2):
            if na == nb: continue
            if _dot(n1, n2) < 0.99: continue           # back to back = butt joint
            a = overlap_area(_project(p1, n1), _project(p2, n2))
            if a >= MIN_AREA:
                hits.append((a, na, nb, key))

    hits.sort(reverse=True)
    print(f'{len(B.solids)} solids, {len(planes)} distinct face planes')
    print(f'coplanar same-facing overlaps >= {MIN_AREA} m2: {len(hits)}')

    def sig(h):
        a, na, nb, key = h
        n = key[:3]
        ax = 'xyz'[max(range(3), key=lambda i: abs(n[i]))]
        lo, hi = sorted((na, nb))
        return f'{lo}|{hi}|{ax}={key[3]:.3f}'

    here = __file__.rsplit('/', 1)[0] or '.'
    bpath = here + '/zfight-baseline.txt'

    if '--baseline' in sys.argv:
        with open(bpath, 'w') as fh:
            fh.write('# Known coplanar face pairs, accepted as of this commit.\n'
                     '# Almost all are wall end-caps meeting a perpendicular wall at a\n'
                     '# building corner -- _wall runs each wall to the exact corner\n'
                     '# coordinate, so its end cap lands on the neighbour\'s outer plane.\n'
                     '# Systemic, harmless at normal viewing distance, and not worth\n'
                     '# insetting every wall to fix.  Regenerate with --baseline only\n'
                     '# when you have looked at what changed and are happy with it.\n')
            for h in sorted(hits, key=sig):
                fh.write(sig(h)+'\n')
        print(f'wrote baseline: {len(hits)} pairs -> {bpath}')
        return 0

    try:
        known = {l.strip() for l in open(bpath) if l.strip() and not l.startswith('#')}
    except FileNotFoundError:
        known = set()
        print('(no baseline; every hit is reported)')

    new = [h for h in hits if sig(h) not in known]
    gone = known - {sig(h) for h in hits}
    for a, na, nb, key in new[:40]:
        n = key[:3]
        ax = 'xyz'[max(range(3), key=lambda i: abs(n[i]))]
        print(f'  NEW  {a:8.3f} m2  {na:<26} {nb:<26} {ax}={key[3]:.3f}')
    print(f'new: {len(new)}   fixed since baseline: {len(gone)}')
    return 1 if new else 0


if __name__ == '__main__':
    sys.exit(main())
