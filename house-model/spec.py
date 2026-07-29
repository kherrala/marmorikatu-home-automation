# Marmorikatu 10 — geometry spec v2, rebuilt from DWG-derived vector extraction
# (1:50 architect PDFs) + elevations. Units m. Origin: outer SW corner of 1krs.
# x: 0=pohjoinen paaty -> 16.98=etela paaty (real compass N->S)
# y: 0=lansi julkisivu (entrance side) -> 7.98=ita julkisivu
# z: 0 = 1krs floor top (+135.90). Kellari +132.86, 2krs +138.91.
import math
EXT = 0.30   # exterior wall (drawn 0.36 with cladding; 0.30 keeps openings crisp)
KXT = 0.34   # kellari concrete+render
INT = 0.10
Z_K = -3.04; Z_2 = 3.01
# Cladding base.  The exterior walls are authored from z=0 (the 1. krs floor) so that every
# opening's zb reads straight off the elevations, but the cladding itself does not stop
# there: it oversails the slab edge and dies ~200 mm lower, level with the bottom of the
# vertical board columns.  julkisivut.pdf gives the line on three elevations -- ITÄ -0.203,
# POHJOINEN -0.205, ETELÄ -0.193 (block) / -0.207 (living wing) -- and on LÄNSI it is hidden
# behind the terrace decks, whose tops sit at exactly -0.193, which agrees.  One constant for
# all of it, within 8 mm of every reading.  Walls get it as a skirt below z0 (see _wall).
Z_CLAD = -0.200
# Yard datums are module-level: build_katos lays the driveway off Z_GRADE too.
Z_GRADE = -0.60
Z_YARD  = -3.25
def yard_z(x,y):
    """Finished ground on the entrance side -- ONE surface for the paving and the lawn alike,
    which is the point: they were on two different functions and stepped 65 mm against each
    other along the shared edge at y=-1.819, which is the seam that read as a gap.
    Two components, both off the site plan: 2.5% in +y (x=0 runs -0.60 at y=0 to -0.80 at
    y=7.98, flat for y<=0), and 90 mm of fall westward across the drive to the plot line
    (-0.60 at the house, +135.21 = -0.69 at the sewer connection on the drive).
    Paving sits 10 mm proud of turf."""
    return Z_GRADE-0.025*max(0.0,y)-0.09*min(1.0,max(0.0,-x/5.0))
H_K = 2.54   # kellari clear (2540)
H_1 = 2.56   # 1krs clear (2560); 2krs slab 2.56->3.01
H_1E= 3.01   # 1krs exterior walls run up over the slab edge (continuous cladding)
H_L = 2.60   # living-wing walls (flat ceiling per owner)
H_2 = 2.58   # 2krs interior (2580)
H_2E= 3.14   # 2krs long-side ext walls up to roof underside.  Was 3.19, which topped them
             # out at 6.20 while the deck top at their outer face (y 0.0 / 7.98) is 6.171 --
             # 29 mm of wall stood proud of the roof the whole length of both sides, and
             # because the walls are cut into segments by the window openings it read as two
             # dashed pale stripes lying on the slope.  3.14 tops them at 6.15: still buried
             # in the 197 mm deck (underside 5.974), just no longer through it.

P1 = [(0,0),(14.28,0),(14.28,3.30),(16.98,3.30),(16.98,7.98),(0,7.98)]
PK = [(0.07,0.07),(13.83,0.07),(13.83,3.37),(16.91,3.37),(16.91,7.91),(0.07,7.91)]
P2 = [(0,0),(10.98,0),(10.98,7.98),(0,7.98)]

def W(kind,a0,a1,zb=None,zt=None):
    if kind in ('door','glassdoor'): zb,zt = 0.0,(zt or 2.10)
    if kind=='win': zb = 0.9 if zb is None else zb; zt = 2.0 if zt is None else zt
    return (kind,a0,a1,zb,zt)

# t0/t1 pull an end back by that much.  Used to butt a wall against the one it meets at a
# corner instead of running through it: if both walls run to the exact corner coordinate they
# interpenetrate over a t x t square, and one wall's END CAP lands on the other's OUTER face,
# same plane and same facing -- a z-fight the full height of every corner in the building.
# Trim by the neighbour's FULL build-up and the cap lands on its inner face instead, back to
# back with it, which is a butt joint and also how it is actually built.  Full build-up means
# t + LINT, not t: exterior walls carry a 22 mm liner on the room side, so trimming only the
# structural thickness just moves the clash onto the liner (which is exactly what happened).
# ConcreteW walls have no liner, so those trim by t alone.  Openings are unaffected:
# ops are absolute coordinates, so only the first/last segment changes length.
LINT=0.022   # liner thickness _wall adds on the room side of WallExt/WallExt2
# m0/m1 MITER an end at 45° instead of a square butt: the wall's full-height end segment is
# cut on the diagonal so its OUTER face runs all the way to the corner point and its INNER
# face stops one thickness short, meeting the perpendicular wall's identical cut back-to-back
# on the corner diagonal. The sign says which face extends to the corner: for an axis-'x' wall
# +1 = the +x face, -1 = the -x face; for axis 'y', +1 = +y, -1 = -y. Always the EXTERIOR
# face, so two walls meeting at an outside corner get opposite-looking signs that share one
# diagonal (e.g. wS.notch m1=-1 + wE m0=+1 at plan 16.98,3.30). A mitered end replaces the old
# t0/t1 butt-trim — pass one or the other, not both.
def wall_x(B,name,x,y0,y1,z0,h,t,ops=(),mat='WallInt',skirt=0.0,inward=None,t0=0.0,t1=0.0,m0=0,m1=0):
    _wall(B,name,'x',x,y0+t0,y1-t1,z0,h,t,ops,mat,skirt,inward,m0,m1)
def wall_y(B,name,y,x0,x1,z0,h,t,ops=(),mat='WallInt',skirt=0.0,inward=None,t0=0.0,t1=0.0,m0=0,m1=0):
    _wall(B,name,'y',y,x0+t0,x1-t1,z0,h,t,ops,mat,skirt,inward,m0,m1)
def _wall(B,name,axis,c,a0,a1,z0,h,t,ops,mat,skirt=0.0,inward=None,m0=0,m1=0):
    ops = sorted(ops,key=lambda o:o[1]); cur=a0; i=0
    # exterior walls get a thin white liner on the room side (texture stays outside).
    # `inward` is +1/-1 towards the room and normally falls out of which side of the building
    # centre the wall sits on -- but that heuristic is written for the house footprint, so any
    # outbuilding has to say which way is in.  The carport's TR.var.e was the case that
    # caught it: the guess put the white liner on the driveway face, which is why that wall
    # rendered white instead of clad.
    # NB: `sd`, not `d` -- the door-leaf thickness below is called `d` and would clobber it
    # part way through the ops loop, which silently collapsed every skirt after a doorway.
    lin=None; sd=None
    if mat in ('WallExt','WallExt2'):
        if inward is None:
            ctr=8.5 if axis=='x' else 4.0
            inward=1 if c<ctr else -1
        sd=inward
        lin=c+sd*(t/2+0.011)
    def emit(nm,lo,hi,zb,zt,mlo=0,mhi=0):
        if hi-lo<=0.005 or zt-zb<=0.005: return
        mit = bool(mlo or mhi)
        if mit:
            # mitered end(s): the structural piece is a slab whose footprint is cut on the
            # 45° diagonal(s). The extending face reaches the raw end; the other retracts t.
            if axis=='x':
                xm,xp=c-t/2,c+t/2
                ylo_m=lo+(t if mlo==1 else 0); ylo_p=lo+(t if mlo==-1 else 0)
                yhi_m=hi-(t if mhi==1 else 0); yhi_p=hi-(t if mhi==-1 else 0)
                poly=[(xm,ylo_m),(xp,ylo_p),(xp,yhi_p),(xm,yhi_m)]
            else:
                ym,yp=c-t/2,c+t/2
                xlo_m=lo+(t if mlo==1 else 0); xlo_p=lo+(t if mlo==-1 else 0)
                xhi_m=hi-(t if mhi==1 else 0); xhi_p=hi-(t if mhi==-1 else 0)
                poly=[(xlo_m,ym),(xhi_m,ym),(xhi_p,yp),(xlo_p,yp)]
            B.slab(nm,poly,z0+zb,z0+zt,mat)
        elif axis=='x': B.box(nm,(c-t/2,c+t/2),(lo,hi),(z0+zb,z0+zt),mat)
        else:           B.box(nm,(lo,hi),(c-t/2,c+t/2),(z0+zb,z0+zt),mat)
        # cladding skirt: the same board carried below z0 over the slab edge, 6 mm proud of
        # the wall plane so it is not coplanar with the slab face it hangs in front of.
        # Only pieces that actually reach z0 get one, which breaks it at every doorway.
        # Skipped on a mitered end — the skirt is a square board and would poke past the cut.
        if skirt>0.005 and sd is not None and zb<=1e-9 and not mit:
            s0,s1 = sorted((c-sd*(t/2+0.006), c-sd*(t/2-0.060)))
            if axis=='x': B.box(nm+'.clad',(s0,s1),(lo,hi),(z0-skirt,z0),mat)
            else:         B.box(nm+'.clad',(lo,hi),(s0,s1),(z0-skirt,z0),mat)
        if lin is not None:
            # Pull the liner back from the wall's own ends. Otherwise its end cap lands exactly on
            # the perpendicular facade's outer plane and shows as a white stripe near the corner.
            # A mitered end pulls it back a full thickness so it stays behind the diagonal cut.
            llo = lo+(t+LINT if mlo else (0.02 if abs(lo-a0)<1e-9 else 0))
            lhi = hi-(t+LINT if mhi else (0.02 if abs(hi-a1)<1e-9 else 0))
            if lhi-llo>0.005:
                if axis=='x': B.box(nm+'.lin',(lin-0.011,lin+0.011),(llo,lhi),(z0+zb,z0+zt),'WallInt')
                else:         B.box(nm+'.lin',(llo,lhi),(lin-0.011,lin+0.011),(z0+zb,z0+zt),'WallInt')
    tag='xfr' if mat in ('WallExt','WallExt2','ConcreteW') else 'ifr'   # trim hides with its wall layer
    def frame(nm,lo,hi,zb,zt):
        if hi-lo<=0.004 or zt-zb<=0.004: return
        if axis=='x': B.box(nm,(c-t/2-0.015,c+t/2+0.015),(lo,hi),(z0+zb,z0+zt),'Frame')
        else:         B.box(nm,(lo,hi),(c-t/2-0.015,c+t/2+0.015),(z0+zb,z0+zt),'Frame')
    for (kind,o0,o1,zb,zt) in ops:
        emit(f'{name}.seg{i}',cur,o0,0,h,mlo=(m0 if abs(cur-a0)<1e-9 else 0)); i+=1
        emit(f'{name}.sill{i}',o0,o1,0,zb)
        emit(f'{name}.lint{i}',o0,o1,zt,h)
        g=0.03
        if kind in ('win','glassdoor'):
            if axis=='x': B.box(f'{name}.glass{i}',(c-g/2,c+g/2),(o0+0.02,o1-0.02),(z0+zb+0.02,z0+zt-0.02),'Glass')
            else:         B.box(f'{name}.glass{i}',(o0+0.02,o1-0.02),(c-g/2,c+g/2),(z0+zb+0.02,z0+zt-0.02),'Glass')
        if kind=='door':
            d=0.04
            if axis=='x': B.box(f'{name}.leaf{i}',(c-d/2,c+d/2),(o0+0.01,o1-0.01),(z0,z0+zt-0.02),'Door')
            else:         B.box(f'{name}.leaf{i}',(o0+0.01,o1-0.01),(c-d/2,c+d/2),(z0,z0+zt-0.02),'Door')
            if axis=='x': B.box(f'{name}.hnd{i}',(c-d/2-0.05,c+d/2+0.05),(o1-0.22,o1-0.08),(z0+0.98,z0+1.02),'Metal')
            else:         B.box(f'{name}.hnd{i}',(o1-0.22,o1-0.08),(c-d/2-0.05,c+d/2+0.05),(z0+0.98,z0+1.02),'Metal')
        # white trim: jambs + head for every opening, bottom board for windows
        f=0.055
        frame(f'{name}.{tag}L{i}',o0,o0+f,zb,zt)
        frame(f'{name}.{tag}R{i}',o1-f,o1,zb,zt)
        frame(f'{name}.{tag}T{i}',o0+f,o1-f,zt-f,zt)
        if kind=='win': frame(f'{name}.{tag}B{i}',o0+f,o1-f,zb,zb+f)
        cur=o1
    emit(f'{name}.seg{i}',cur,a1,0,h,mlo=(m0 if abs(cur-a0)<1e-9 else 0),mhi=m1)

def face(B,nm,axis,fc,ind,a0,a1,z0,z1,mat='Frame',pr=0.015,dp=0.030):
    """Board applied flat to an exterior wall face.
       NB dp is how far the board bites INTO the wall and must be positive.  Every caller
       used to pass -0.01, which stood the boards 10 mm clear of the wall face instead --
       a shadow slot behind every clad panel on the house that you could see the siding
       through at any grazing angle.
       axis 'y' = wall plane at constant y running along x (as wall_y), 'x' = the transpose.
       fc = the outer plane, ind = +1/-1 pointing from that plane into the wall.
       pr = how far the board stands proud of fc, dp = how far it bites into the wall."""
    if a1-a0<=0.004 or z1-z0<=0.004: return
    lo,hi = sorted((fc-ind*pr, fc+ind*dp))
    if axis=='y': B.box(nm,(a0,a1),(lo,hi),(z0,z1),mat)
    else:         B.box(nm,(lo,hi),(a0,a1),(z0,z1),mat)

def bed(B,nm,x0,y0,w,l,axis='y',mat='BedWhite'):
    x1,y1=(x0+w,y0+l) if axis=='y' else (x0+l,y0+w)
    B.box(nm+'.base',(x0,x1),(y0,y1),(0.12,0.35),'WoodFurn')
    B.box(nm+'.matt',(x0+0.03,x1-0.03),(y0+0.03,y1-0.03),(0.35,0.55),mat)
    if axis=='y':
        B.box(nm+'.head',(x0,x1),(y1-0.06,y1),(0.12,1.0),'WoodFurn')
        B.box(nm+'.pillow',(x0+0.08,x1-0.08),(y1-0.55,y1-0.15),(0.55,0.65),'Ceramic')
    else:
        B.box(nm+'.head',(x0,x0+0.06),(y0,y1),(0.12,1.0),'WoodFurn')
        B.box(nm+'.pillow',(x0+0.15,x0+0.55),(y0+0.08,y1-0.08),(0.55,0.65),'Ceramic')
def table(B,nm,x0,x1,y0,y1,h=0.74,mat='WoodFurn',leg=0.05):
    B.box(nm+'.top',(x0,x1),(y0,y1),(h-0.04,h),mat)
    for i,(lx,ly) in enumerate([(x0,y0),(x1-leg,y0),(x0,y1-leg),(x1-leg,y1-leg)]):
        B.box(f'{nm}.leg{i}',(lx,lx+leg),(ly,ly+leg),(0,h-0.04),mat)
def chair(B,nm,x,y,rot=0,mat='FabricBlue'):
    s=0.44; B.box(nm+'.seat',(x-s/2,x+s/2),(y-s/2,y+s/2),(0.24,0.45),mat); d=0.06
    if   rot==0:   B.box(nm+'.back',(x-s/2,x+s/2),(y+s/2-d,y+s/2),(0.45,0.92),mat)
    elif rot==180: B.box(nm+'.back',(x-s/2,x+s/2),(y-s/2,y-s/2+d),(0.45,0.92),mat)
    elif rot==90:  B.box(nm+'.back',(x-s/2,x-s/2+d),(y-s/2,y+s/2),(0.45,0.92),mat)
    else:          B.box(nm+'.back',(x+s/2-d,x+s/2),(y-s/2,y+s/2),(0.45,0.92),mat)
def wardrobe(B,nm,x0,x1,y0,y1,h=2.15): B.box(nm,(x0,x1),(y0,y1),(0,h),'Cabinet')
def rug(B,nm,x0,x1,y0,y1,mat='Rug'): B.box(nm,(x0,x1),(y0,y1),(0.024,0.038),mat)  # above the Room_ patch (no z-fight)
def plant(B,nm,x,y,s=1.0):
    B.cyl(nm+'.pot',x,y,0,0.35*s,0.16*s,'Pot'); B.cyl(nm+'.tr',x,y,0.35*s,0.7*s,0.04*s,'WoodFurn')
    B.sph(nm+'.fol',x,y,0.95*s,0.32*s,'Plant')
def sofa(B,nm,x0,x1,y0,y1,backside,mat='SofaWhite'):
    B.box(nm+'.seat',(x0,x1),(y0,y1),(0.15,0.42),mat); t=0.16
    if backside=='N': B.box(nm+'.back',(x0,x1),(y1-t,y1),(0.42,0.78),mat)
    if backside=='S': B.box(nm+'.back',(x0,x1),(y0,y0+t),(0.42,0.78),mat)
    if backside=='E': B.box(nm+'.back',(x1-t,x1),(y0,y1),(0.42,0.78),mat)
    if backside=='W': B.box(nm+'.back',(x0,x0+t),(y0,y1),(0.42,0.78),mat)
def toilet(B,nm,x,y,rot='S'):
    B.box(nm+'.tank',(x-0.19,x+0.19),(y+0.12,y+0.27) if rot=='S' else (y-0.27,y-0.12),(0.2,0.75),'Ceramic')
    B.cyl(nm+'.bowl',x,y-0.05 if rot=='S' else y+0.05,0.2,0.42,0.19,'Ceramic')

# ================================================================= KELLARI
def build_kellari(B):
    B.floor='kellari'
    B.slab('K.slab',PK,Z_K-0.30,Z_K,'Concrete')
    t=KXT; h=H_K
    wall_y(B,'K.wS',0.07+t/2,0.07,13.83,Z_K,h,t,mat='ConcreteW',m0=-1)     # a1 still butts K.wNW (its +x offset makes a miter there irregular)
    wall_x(B,'K.wNW',13.83+t/2,0.07,3.37,Z_K,h,t,          # notch west wall, exit door
           ops=[W('door',2.18,3.19,0,2.05)],mat='ConcreteW')
    wall_y(B,'K.wNN',3.37+t/2,13.83,16.91,Z_K,h,t,mat='ConcreteW',m1=-1)   # a0 butts K.wNW (inside corner)
    wall_x(B,'K.wE',16.91-t/2,3.37,7.91,Z_K,h,t,
           ops=[W('win',5.08,6.18,1.75,2.30)],mat='ConcreteW',m0=1,m1=1)
    wall_y(B,'K.wN',7.91-t/2,0.07,16.91,Z_K,h,t,m0=1,m1=1,
           ops=[W('win',11.84,12.94,1.75,2.30),W('win',14.54,15.64,1.75,2.30)],mat='ConcreteW')
    wall_x(B,'K.wW',0.07+t/2,0.07,7.91,Z_K,h,t,mat='ConcreteW',m0=-1,m1=-1)
    wall_x(B,'K.div',10.805,0.41,7.57,Z_K,h,0.27,
           ops=[W('door',2.28,3.19,0,2.05)],mat='ConcreteW')
    # WC in the NW corner (built after the drawings; west side per owner)
    wall_x(B,'K.wc.e',2.00,0.41,2.10,Z_K,H_K,INT)
    wall_y(B,'K.wc.s',2.10,0.41,2.05,Z_K,H_K,INT,ops=[W('door',0.95,1.80)])
    B.room('Room_kellari_WC',[(0.41,0.41),(1.95,0.41),(1.95,2.05),(0.41,2.05)],'Tile',z=Z_K)
    B.room('Room_kellari_VAR1',[(0.41,2.15),(2.10,2.15),(2.10,0.41),(10.67,0.41),(10.67,7.57),(0.41,7.57)],'ConcreteDark',z=Z_K)
    B.room('Room_kellari_VAR2',[(10.94,0.41),(13.66,0.41),(13.66,3.54),(16.57,3.54),(16.57,7.57),(10.94,7.57)],'ConcreteF',z=Z_K)
    B.box('K.shelfV2',(15.2,16.5),(6.9,7.5),(Z_K,Z_K+2.0),'WoodFurn')
    B.box('K.bench',(11.2,13.2),(0.5,1.1),(Z_K,Z_K+0.9),'WoodFurn')
    # --- big room as rec room (owner): billiard N end, screen+sofa S end, desk SW
    B.zoff=Z_K
    toilet(B,'K.wc.wc',0.85,0.72,'N')
    B.box('K.wc.basin',(1.55,1.95),(1.45,1.77),(0.55,0.87),'Ceramic')
    # layout per owner: the two zones sit on opposite diagonals of the big room.
    # Media end = S end of the W wall (bottom-left on a north-up plan): 3x2 m screen,
    # olive corner sofa in front of it, AV cabinet beside it, projector on the ceiling
    # behind the sofa. Billiard end = toward the NE corner (top-right), blue felt,
    # 3 black pendants over it, cue rack + TV on the N gable, curtains on the E wall.
    B.box('K.pool.body',(2.05,4.55),(4.75,6.15),(0.55,0.75),'DarkWood')
    B.box('K.pool.felt',(2.17,4.43),(4.87,6.03),(0.75,0.78),'PoolBlue')
    B.box('K.pool.railW',(2.05,2.17),(4.75,6.15),(0.75,0.83),'DarkWood')
    B.box('K.pool.railE',(4.43,4.55),(4.75,6.15),(0.75,0.83),'DarkWood')
    B.box('K.pool.railS',(2.17,4.43),(4.75,4.87),(0.75,0.83),'DarkWood')
    B.box('K.pool.railN',(2.17,4.43),(6.03,6.15),(0.75,0.83),'DarkWood')
    for i,(lx,ly) in enumerate([(2.10,4.80),(4.34,4.80),(2.10,5.99),(4.34,5.99)]):
        B.box(f'K.pool.leg{i}',(lx,lx+0.16),(ly,ly+0.16),(0,0.55),'DarkWood')
    B.box('K.cuerack',(0.43,0.47),(3.30,4.10),(1.05,1.95),'WoodFurn')          # cues on the N gable
    B.box('K.screen.frame',(6.30,9.30),(0.42,0.46),(0.32,2.36),'TVBlack')      # 3x2 m projection screen, S end of W wall
    B.box('K.screen.face',(6.40,9.20),(0.46,0.475),(0.42,2.26),'White')
    sofa(B,'K.sofaA',6.85,9.35,2.45,3.40,'N','SofaGreen')                      # olive corner sofa facing screen
    sofa(B,'K.sofaB',6.85,7.75,1.30,2.45,'W','SofaGreen')
    rug(B,'K.rug',6.60,9.50,0.9,2.4)
    B.box('K.media',(9.60,10.10),(0.44,0.80),(0,0.40),'Cabinet')               # AV cabinet beside screen
    B.box('K.curtain',(2.15,10.55),(7.44,7.52),(0.05,2.35),'Curtain')          # blackout curtains, E wall
    B.box('K.tv',(0.43,0.47),(4.60,5.60),(1.20,1.78),'TVBlack')                # TV on the N gable
    B.cyl('K.disco.cord',6.20,4.10,2.14,2.44,0.008,'Metal')
    B.sph('K.disco',6.20,4.10,1.94,0.20,'Metal')                               # disco ball
    B.box('K.proj',(7.50,8.10),(4.10,4.60),(2.02,2.32),'TVBlack')              # ceiling projector, behind the sofa
    B.cyl('K.proj.mount',7.80,4.35,2.32,2.52,0.02,'Metal')
    table(B,'K.desk',9.10,10.50,6.60,7.35,0.74)                                # office desk, east side
    B.box('K.monitor',(9.45,10.15),(7.20,7.24),(0.86,1.28),'TVBlack')
    chair(B,'K.deskch',9.80,6.05,180)
    B.zoff=0.0

# ================================================================= 1. KRS
def build_krs1(B):
    B.floor='1krs'
    B.slab('F1.slab',P1,-0.50,0,'Concrete')
    e=EXT/2
    # exterior walls — openings from vector extraction
    # ---- openings: plan (1:50) gives the horizontal extents, julkisivut.pdf (1:100) the z bands.
    # Elevation calibration: LANSI  x=(ptx-177.2)/S    z=(289.80-pty)/S-0.08
    #                        ETELA  y=(ptx-882.81)/S   z=(289.80-pty)/S-0.08
    #                        ITA    x=(547.5-ptx)/S    z=(697.70-pty)/S-0.08
    #                        POHJ   y=36.651-ptx/S     z=(686.95-pty)/S-0.08     (S=28.3465 pt/m)
    # Ground-floor head height is a constant 2.160 m on every facade.
    wall_y(B,'F1.wS.blk',0+e,0,10.98,0,H_1E,EXT,mat='WallExt',skirt=-Z_CLAD,m0=-1,ops=[
        W('win',1.640,2.739,0.74,1.25),                      # MH: strip window inside the slat column
        W('glassdoor',4.889,5.203,0,2.16),                   # entrance sidelight (left of the leaf)
        W('door',5.203,6.230,0,2.16),                        # front door leaf (4 panels, LANSI elev)
        W('win',8.340,9.440,0.74,1.25)])                     # kitchen: strip window inside the slat column
    wall_y(B,'F1.wS.liv',0+e,10.98,14.28,0,H_L,EXT,mat='WallExt',skirt=-Z_CLAD,m1=-1,ops=[
        W('win',11.291,12.090,0.59,2.16),                    # RUOKAILU glazing: three panes,
        W('win',12.190,12.989,0.59,2.16),                    # mullions 12.090-12.190 and
        W('win',13.090,13.891,0.59,2.16)])                   # 12.989-13.090 (LANSI elev)
    wall_x(B,'F1.wE.din',14.28-e,0,3.30,0,H_L,EXT,mat='WallExt',skirt=-Z_CLAD,m0=1,ops=[
        W('win',0.390,1.191,0.59,2.16),W('win',1.289,2.090,0.59,2.16),
        W('glassdoor',2.284,3.194,0,2.16)])                  # terrace door at notch corner (ETELA elev)
    wall_y(B,'F1.wS.notch',3.30+e,14.28,16.98,0,H_L,EXT,mat='WallExt',skirt=-Z_CLAD,m1=-1,ops=[
        W('win',14.289,15.390,0.40,2.16),                    # OH glazing over the terrace, two panes,
        W('win',15.490,16.589,0.40,2.16)])                   # mullion 15.390-15.490 (LANSI elev)
    wall_x(B,'F1.wE',16.98-e,3.30,7.98,0,H_L,EXT,mat='WallExt',skirt=-Z_CLAD,m0=1,m1=1,ops=[
        W('win',3.690,4.791,0.40,2.16),                      # OH south glazing, three panes,
        W('win',4.889,5.990,0.40,2.16),                      # mullions 4.791-4.889 and
        W('win',6.091,7.190,0.40,2.16)])                     # 5.990-6.091 (ETELA elev)
    # ---- living-wing glazed bands, cladding layer.
    # The plan gives the structural piers (~100 mm) and spec'ing them as separate openings is right,
    # but the elevations show each run as ONE continuous band: a 145 mm white cover board over every
    # pier (LANSI 12.086-12.231 / 12.986-13.130, ETELA 1.167-1.312 / 4.769-4.914 / 5.969-6.113),
    # vertical boarding on the 0.39 m corner piers, and a 46 mm head trim at z 2.160-2.206 running
    # the whole length (LANSI 11.292-14.323 and 14.323-16.817, ETELA 0.183-2.106 / 2.272-3.210 /
    # 3.277-7.207). Without these the siding shows through the band and it reads as punched windows.
    for a0,a1 in ((12.085,12.195),(12.984,13.095)):
        face(B,f'F1.mull.liv{a0:.3f}','y',0.0,1,a0,a1,0.59,2.16)
    face(B,'F1.mull.din','x',14.28,-1,1.186,1.294,0.59,2.16)
    face(B,'F1.mull.notch','y',3.30,1,15.385,15.495,0.40,2.16)
    for a0,a1 in ((4.786,4.894),(5.985,6.096)):
        face(B,f'F1.mull.wE{a0:.3f}','x',16.98,-1,a0,a1,0.40,2.16)
    # corner piers: vertical board cladding, same motif as the entrance panel and the slat columns.
    # Each pair wraps one outside corner, so the boards overlap slightly around it.
    # brd.liv starts at 13.891, the right jamb of the last RUOKAILU pane, NOT at 13.905: the
    # 14 mm between the two left a slot of bare horizontal siding recessed 70 mm behind both
    # the window trim and the boards, and inside the terrace you look straight down it.  The
    # LÄNSI board hatch runs 13.876..14.353 (clipped at the 0.798 rail line), so on the drawing
    # the pier overlaps the trim rather than stopping short of it.  brd.notch already does this
    # correctly -- its 16.589 is exactly the neighbouring jamb.
    face(B,'F1.brd.liv'  ,'y',0.0  , 1,13.891,14.35 ,0.59,2.16,'Slat',0.07,0.01)
    face(B,'F1.brd.din'  ,'x',14.28,-1,-0.07 , 0.405,0.59,2.16,'Slat',0.07,0.01)
    face(B,'F1.brd.notch','y',3.30 , 1,16.589,17.05 ,0.40,2.16,'Slat',0.07,0.01)
    face(B,'F1.brd.wE'   ,'x',16.98,-1, 3.23 , 3.720,0.40,2.16,'Slat',0.07,0.01)
    # continuous head trim, proud of the boards so it caps the whole run
    face(B,'F1.head.liv'  ,'y',0.0  , 1,11.286,14.35 ,2.16,2.206,pr=0.075)
    face(B,'F1.head.notch','y',3.30 , 1,14.28 ,16.98 ,2.16,2.206,pr=0.075)
    face(B,'F1.head.din1' ,'x',14.28,-1,-0.07 , 2.106,2.16,2.206,pr=0.075)
    face(B,'F1.head.din2' ,'x',14.28,-1, 2.272, 3.210,2.16,2.206,pr=0.075)
    face(B,'F1.head.wE'   ,'x',16.98,-1, 3.277, 7.207,2.16,2.206,pr=0.075)
    wall_y(B,'F1.wN.liv',7.98-e,10.98,16.98,0,H_L,EXT,mat='WallExt',skirt=-Z_CLAD,m1=1,
        ops=[W('win',11.839,12.940,1.65,2.16),W('win',14.540,15.640,1.65,2.16)])  # ITA: high strip band
    wall_y(B,'F1.wN.blk',7.98-e,0,10.98,0,H_1E,EXT,mat='WallExt',skirt=-Z_CLAD,m0=1,ops=[
        W('win',2.840,3.941,1.65,2.16),                      # PH: high short strip (ITA elev)
        W('win',7.040,7.541,0.74,2.16)])                     # KHH tall narrow strip (ITA elev)
    # East facade only: the cladding does not stop at Z_CLAD along this side.  The ground falls
    # the length of it and ITA carries the boarding down after it in two steps.  The first bay
    # is at Z_CLAD, which is why this went unnoticed -- it matches the rest of the house.
    #
    # Step DEPTHS are datum-independent and so are the reliable numbers: -0.751 and -0.879,
    # the first being exactly five 150.5 mm board courses.  Levels below are those depths hung
    # off Z_CLAD, whose ITA reading (-0.203) was measured independently.
    #
    # THE TRAP, and it cost three wrong answers: pdfplumber's `lines` carry BOTTOM-UP y0/y1,
    # but the elevation transforms in MODELING.md §5 are written for TOP-DOWN pty.  Feed
    # bottom-up values into `z = (289.80 - pty)/S - 0.08` and every z comes out MIRRORED about
    # the datum -- silently, and looking entirely plausible.  A foot read that way at -0.673 is
    # really +5.65, near the top of the wall.  Anchor instead on the KHH window, whose 1.422 m
    # height and x 7.040..7.541 both match this model exactly:
    #     z = (pty - 146.52)/S        with pty straight off line.y0   (bottom-up)
    # That lands both roof lines within 0.17 m and reproduces the window to 2 mm.
    # Same band as the skirt _wall builds: 6 mm proud of the wall plane, 66 mm deep.
    ECS=tuple(sorted((7.83+EXT/2-0.060,7.83+EXT/2+0.006)))
    for i,(sx0,sx1,zf) in enumerate((( 4.810,11.690,Z_CLAD-0.751),
                                     (11.690,16.980,Z_CLAD-0.751-0.879))):
        B.box(f'F1.wN.step{i}',(sx0,sx1),ECS,(zf,Z_CLAD),'WallExt')
    # LP vertical-slat column at the KHH/KPH window stack (ITA elevation)
    B.box('F1.slat.c.lo',(7.04,7.54),(7.97,8.05),(-0.45,0.74),'Slat')
    B.box('F1.slat.c.mid',(7.04,7.54),(7.97,8.05),(2.16,3.01),'Slat')
    # LANSI: the two strip windows sit in full-height vertical-slat columns of the same width.
    # The upper panel runs the whole way to the 2. krs window band, not to 3.01: in
    # julkisivut.pdf the horizontal siding lap lines (150 mm pitch, pairs at z 3.222/3.243,
    # 3.374/3.395, 3.522/3.543) stop dead at x 1.589 and pick up again at 2.757, so the
    # facade is not sided across the column anywhere below the 2. krs sill.  The vertical
    # board hatch itself is drawn in two bands, 1.296..3.201 and 3.250..3.501, split only by
    # a 49 mm trim line, and the opening above starts at 3.550 -- so the boarding is
    # continuous to Z_W2 and the old 3.01 top left ~540 mm of siding showing through.
    Z_W2 = 3.550                                          # underside of the 2. krs opening band
    for nm,(sx0,sx1) in (('mh',(1.640,2.739)),('kit',(8.340,9.440))):
        B.box(f'F1.slat.{nm}.lo',(sx0,sx1),(-0.07,0.01),(Z_CLAD,0.74),'Slat')
        B.box(f'F1.slat.{nm}.hi',(sx0,sx1),(-0.07,0.01),(1.25,Z_W2),'Slat')
    # LANSI: the front door sits in a recessed vertical-board entrance panel
    B.box('F1.ent.l',(4.254,4.889),(-0.07,0.01),(Z_CLAD,2.27),'Slat')
    B.box('F1.ent.r',(6.230,6.865),(-0.07,0.01),(Z_CLAD,2.27),'Slat')
    B.box('F1.ent.t',(4.254,6.865),(-0.07,0.01),(2.16,2.27),'Slat')
    # POHJOINEN: matching slat column under/over the stacked MH window.  Cleaner than LANSI
    # -- the hatch is one unbroken band z 2.204..3.548 between the same y 1.627..2.756 edges,
    # with no trim line and no jog, and the 2. krs opening starts at 3.548.  Same fix.
    B.box('F1.slat.n.lo',(-0.07,0.01),(1.640,2.739),(Z_CLAD,0.54),'Slat')
    B.box('F1.slat.n.hi',(-0.07,0.01),(1.640,2.739),(2.16,Z_W2),'Slat')
    # roof-access ladder on the east facade at x~9.0-9.4 (drawn in ITA elevation).
    # It stands CLEAR OF THE EAVE, not flat on the wall: the eave runs out to y 8.681 and the
    # gutter to 8.811, so a ladder tight to the cladding at y 8.02 could never reach the roof --
    # it would run into the underside of the overhang.  Rails at y 8.84..8.88, 860 mm off the
    # wall, on standoff brackets.  Elevation cannot show a standoff, which is how it stayed
    # wrong.  The top stops at 6.00, level with the eave, where the roof ladder takes over.
    LADY0,LADY1 = 8.840,8.880
    for lx in (9.01,9.41):
        B.box(f'F1.ladder.r{lx:.2f}',(lx-0.02,lx+0.02),(LADY0,LADY1),(-0.40,6.00),'Metal')
    nrung=int((6.0-0.0)/0.30)
    for i in range(nrung+1):
        B.box(f'F1.ladder.g{i}',(9.01,9.41),(LADY0+0.005,LADY1-0.005),
              (0.0+i*0.30-0.015,0.0+i*0.30+0.015),'Metal')
    for i,bz in enumerate((0.60,2.10,3.60,5.10)):                      # standoff brackets
        for lx in (9.01,9.41):
            B.box(f'F1.ladder.br{i}{lx:.0f}',(lx-0.018,lx+0.018),(7.98,LADY0),(bz-0.018,bz+0.018),'Metal')
    # It does not stop at the eave: ITA draws the ladder continuing up the block roof, 12 more
    # rungs from z 6.143 to 7.184 at 0.0946 z-spacing.  That spacing IS the giveaway -- 300 mm
    # along a 1:3 slope projects to 300*sin(atan(1/3)) = 94.9 mm in elevation, so it is the
    # same 300 mm ladder lying on the roof, seen foreshortened.  Converting those z back through
    # mz() puts it between y 8.064 and y 4.941.  From its head a 2.148 m member runs across at
    # z 7.293..7.342, from x 8.902 to x 11.051 -- which is exactly the chimney's near face, so
    # it is the walkway to the flue.  None of this was modelled.
    RLX0,RLX1 = 9.019,9.403
    # rails start at the eave (y 8.681) so the roof run meets the wall ladder instead of
    # beginning in mid-slope; the rungs stay where ITA draws them, from y 8.064 up.
    RLY0,RLY1 = 8.681,4.941
    RG0 = 8.064
    def rmz(y): return 7.501-abs(y-3.99)/3.0
    for j,(rx0,rx1) in enumerate([(RLX0,RLX0+0.036),(RLX1-0.036,RLX1)]):     # side rails
        B.roofquad(f'F1.rlad.rail{j}',[(rx0,RLY0,rmz(RLY0)+0.075),(rx1,RLY0,rmz(RLY0)+0.075),
                                       (rx1,RLY1,rmz(RLY1)+0.075),(rx0,RLY1,rmz(RLY1)+0.075)],0.035,'Metal')
    NR=12
    for i in range(NR):
        ry=RG0-(RG0-RLY1)*i/(NR-1)
        B.box(f'F1.rlad.g{i}',(RLX0,RLX1),(ry-0.015,ry+0.015),(rmz(ry)+0.040,rmz(ry)+0.075),'Metal')
    # the run to the flue is a ladder too, not a plank: two rails with rungs across, laid flat
    # on the roof at constant y so it sits at constant z.
    for j,(cy0,cy1) in enumerate([(4.530,4.566),(4.634,4.670)]):
        B.box(f'F1.rlad.crail{j}',(8.902,11.051),(cy0,cy1),(7.293,7.342),'Metal')
    ncr=int((11.051-8.902)/0.30)
    for i in range(ncr+1):
        cx=8.902+i*(11.051-8.902)/ncr
        B.box(f'F1.rlad.cg{i}',(cx-0.015,cx+0.015),(4.530,4.670),(7.300,7.330),'Metal')
    wall_x(B,'F1.wW',0+e,0,7.98,0,H_1E,EXT,mat='WallExt',skirt=-Z_CLAD,m0=-1,m1=-1,ops=[
        W('win',1.640,1.878,0.54,2.16),                      # MH north window, narrow pane, and
        W('win',1.977,2.739,0.54,2.16),                      # main pane; mullion 1.878-1.977 (POHJ elev)
        W('door',4.186,5.195,0,2.16),                        # TEKN exterior door (POHJOINEN elev)
        W('win',6.440,6.941,1.65,2.16)])                     # sauna high strip (POHJOINEN elev)
    # interior walls
    wall_y(B,'F1.nb.w',5.45,0.30,4.41,0,H_1,INT,ops=[W('door',3.55,4.30)])   # PH door to hall
    wall_y(B,'F1.nb.e',5.60,4.41,9.64,0,H_1,INT,
        ops=[W('door',6.85,7.60)])                           # KHH door

    wall_x(B,'F1.lh_ph',2.44,5.45,7.68,0,H_1,INT,ops=[W('door',5.75,6.45)])
    wall_x(B,'F1.ph_khh',4.44,5.45,7.68,0,H_1,INT,ops=[W('door',6.55,7.30)])
    wall_x(B,'F1.khh_vh',7.92,5.60,7.68,0,H_1,INT,ops=[W('door',6.35,7.10)])  # VH entered from KHH
    wall_x(B,'F1.vh_st',9.64,5.55,7.68,0,H_1,INT)
    wall_x(B,'F1.st_liv',10.84,5.52,7.68,0,2.56,INT)
    wall_x(B,'F1.tekn_wc',2.49,3.90,5.45,0,H_1,INT)
    wall_x(B,'F1.wc_et',4.10,3.90,5.45,0,H_1,INT,ops=[W('door',4.35,5.10)])
    wall_y(B,'F1.mh_n',3.90,0.30,4.10,0,H_1,INT)
    wall_x(B,'F1.mh_e',3.75,0.30,3.90,0,H_1,INT,ops=[W('door',2.55,3.40)])
    wall_y(B,'F1.tk_n',2.50,3.75,6.34,0,H_1,INT,ops=[W('door',5.00,5.90)])
    wall_y(B,'F1.vh2_n',2.50,6.34,7.92,0,H_1,INT)
    wall_x(B,'F1.tk_vh2',6.34,0.30,2.45,0,H_1,INT,ops=[W('door',0.95,1.70)])  # VH2 from the vestibule
    wall_x(B,'F1.kit_et',7.92,0.77,4.07,0,H_1,INT)           # kitchen wall, passage N of it
    # rooms
    R=B.room
    R('Room_1krs_LH',[(0.30,5.50),(2.39,5.50),(2.39,7.68),(0.30,7.68)],'TileDark')
    R('Room_1krs_PH',[(2.49,5.50),(4.39,5.50),(4.39,7.68),(2.49,7.68)],'Tile')
    R('Room_1krs_KHH',[(4.49,5.65),(7.87,5.65),(7.87,7.68),(4.49,7.68)],'Tile')
    R('Room_1krs_VH',[(7.97,5.65),(9.59,5.65),(9.59,7.68),(7.97,7.68)],'Wood')
    R('Room_1krs_PORRAS',[(9.69,5.55),(10.79,5.55),(10.79,7.68),(9.69,7.68)],'Wood')
    R('Room_1krs_TEKN',[(0.30,3.95),(2.44,3.95),(2.44,5.40),(0.30,5.40)],'ConcreteF')
    R('Room_1krs_WC',[(2.54,3.95),(4.05,3.95),(4.05,5.40),(2.54,5.40)],'Tile')
    R('Room_1krs_MH',[(0.30,0.30),(3.70,0.30),(3.70,3.85),(0.30,3.85)],'Wood')
    R('Room_1krs_TK',[(4.80,0.30),(6.29,0.30),(6.29,2.45),(4.80,2.45)],'Tile')
    R('Room_1krs_VH2',[(6.39,0.30),(7.87,0.30),(7.87,2.45),(6.39,2.45)],'Wood')
    R('Room_1krs_ET',[(3.80,0.30),(4.75,0.30),(4.75,2.55),(7.87,2.55),(7.87,5.40),(3.80,5.40)],'Wood')  # one L-shaped lobby
    # open-plan wing split into three zones (no walls) so lights map per area
    R('Room_1krs_KT',[(7.97,0.30),(10.92,0.30),(10.92,5.52),(7.97,5.52)],'Wood')
    R('Room_1krs_RUOKAILU',[(10.96,0.30),(14.06,0.30),(14.06,3.43),(10.96,3.43)],'Wood')
    R('Room_1krs_OH',[(10.96,3.47),(16.68,3.47),(16.68,7.68),(10.96,7.68)],'Wood')
    # stairs 1->2: U with winders; east flight up N, west flight arrives 2krs
    riser=3.01/17
    for i in range(1,7):                                    # treads 1-6 east flight
        y0=5.55+(i-1)*0.25
        B.box(f'F1.stA{i}',(9.74,10.60),(y0,y0+0.25),(0,i*riser),'StairWood')
    B.box('F1.stDiv',(9.66,9.72),(5.60,7.66),(0,2.85),'Railing')        # between flights
    B.box('F1.stCl',(8.81,9.55),(5.68,6.60),(0,1.55),'Cabinet')         # closet under west flight
    # white tiled mass fireplace facing the living room + steel flue (interior photo)
    B.box('F1.fire',(10.92,11.55),(5.45,6.35),(0,1.55),'Ceramic')
    B.box('F1.firebox',(11.49,11.57),(5.62,6.18),(0.35,0.90),'TVBlack')
    B.cyl('F1.flue',11.23,5.90,1.55,2.60,0.13,'Metal')
    # ---- fixtures & furniture
    B.box('F1.kiuas',(1.95,2.31),(5.58,5.94),(0,0.95),'Metal')          # sauna stove by door
    B.box('F1.laut.hi',(0.34,0.92),(5.50,7.64),(1.00,1.15),'SaunaWood')
    B.box('F1.laut.lo',(0.92,1.42),(5.50,7.64),(0.55,0.70),'SaunaWood')
    B.box('F1.laut.n',(0.92,2.35),(7.06,7.64),(1.00,1.15),'SaunaWood')
    for i,x in enumerate([2.90,3.70]):
        B.cyl(f'F1.shpole{i}',x,7.62,0,2.1,0.02,'Metal'); B.box(f'F1.shhead{i}',(x-0.1,x+0.1),(7.46,7.64),(2.05,2.08),'Metal')
    B.box('F1.khh.counter',(4.55,6.60),(7.05,7.68),(0.86,0.91),'Counter')
    B.box('F1.khh.wash',(4.60,5.20),(7.08,7.65),(0,0.85),'Appliance')
    B.box('F1.khh.dry',(5.26,5.86),(7.08,7.65),(0,0.85),'Appliance')
    B.cyl('F1.khh.sink',6.25,7.35,0.80,0.90,0.18,'Ceramic')
    B.box('F1.khh.tall',(7.30,7.86),(7.08,7.68),(0,2.10),'Cabinet')
    toilet(B,'F1.wc.wc',3.05,4.95,'S'); B.box('F1.wc.basin',(3.60,4.00),(5.05,5.37),(0.55,0.87),'Ceramic')
    # ---- tekninen tila -------------------------------------------------------------------
    # Both machines stand against the wall opposite the door, i.e. the south wall (inner face
    # x=2.44); the door is in the north facade. IV.pdf puts the air-handling unit in the SE
    # corner (x 1.87..2.43, y 4.85..5.40) with its ducts rising north; LVI '1 KRS.pdf' shows the
    # ground-source heat pump next to it on the same wall, inside the block marked
    # "VARAUS MLP-PUTKILLE 900x600x200" that runs x 1.90..2.42, y 4.06..5.38.
    # Swegon CASA W130: 598 x 605 x 1169 mm, floor-standing on its jalusta, powder-coated white.
    B.box('F1.tekn.iv',(1.835,2.44),(4.790,5.390),(0.09,1.17),'White')
    B.box('F1.tekn.iv.base',(1.885,2.42),(4.840,5.340),(0,0.09),'Metal')
    B.box('F1.tekn.iv.door',(1.826,1.835),(4.830,5.350),(0.20,1.11),'Cabinet')     # hinged front
    # top spigots, laid out as the datasheet's plan view: 4 x d160 + the d100 recirculation
    for tag,dx,dy,r in (('tu',-0.189,-0.196,0.080),('ja',0.189,-0.196,0.080),
                        ('po',-0.189, 0.052,0.080),('ul',0.189, 0.052,0.080),
                        ('ki', 0.000,-0.019,0.050)):
        B.cyl(f'F1.tekn.iv.{tag}',2.1375+dx,5.090+dy,1.17,1.72,r,'Metal')
    B.tube('F1.tekn.iv.main',[(1.95,4.960),(0.93,4.960)],0.085,1.72,'Metal',sides=16)
    # maalämpöpumppu with integrated DHW cylinder, alongside on the same wall
    B.box('F1.tekn.mlp',(1.835,2.44),(4.130,4.730),(0,1.72),'Appliance')
    B.box('F1.tekn.mlp.disp',(1.826,1.835),(4.280,4.580),(1.30,1.46),'TVBlack')
    B.box('F1.tekn.pumps',(1.60,1.83),(4.180,4.680),(0.55,0.95),'Metal')           # P3 pump group
    for py in (4.26,4.60):                                                          # collector legs
        B.cyl(f'F1.tekn.mlp.p{py:.2f}',1.79,py,0,0.62,0.028,'Metal')
    # sähkökeskus, on the east wall just inside the door
    B.box('F1.tekn.panel',(0.34,0.97),(5.29,5.40),(0.90,1.70),'Cabinet')
    rug(B,'F1.mh.rug',0.8,3.4,0.7,3.3)
    sofa(B,'F1.mh.sofa',0.45,1.40,0.70,2.70,'W','SofaGreen')
    B.cyl('F1.mh.side',1.80,1.05,0,0.50,0.25,'WoodFurn')
    wardrobe(B,'F1.mh.ward',2.73,3.12,0.90,2.60,2.10)                   # per plan, e-wall closet
    plant(B,'F1.mh.plant',3.30,3.40,0.8)
    B.box('F1.tk.bench',(3.82,4.16),(0.50,2.10),(0.15,0.48),'WoodFurn')   # entry bench on the west wall
    B.box('F1.tk.rack',(3.81,3.87),(0.60,2.00),(1.60,1.92),'WoodFurn')    # coat rack above the bench
    for i,y in enumerate([0.45,1.90]):
        B.box(f'F1.vh2.sh{i}',(6.44,7.82),(y,y+0.42),(0,2.0),'Cabinet')
    B.box('F1.et.sk',(7.30,7.86),(2.60,4.05),(0,2.10),'Cabinet')        # SK/pantry column hall side
    # kitchen per 1krs plan: L-counter on the hall wall (x7.97) with AP+sink,
    # VK/JK + PA tall units off the street wall, island with hob at x9.74-10.74
    B.box('F1.kit.base',(7.99,8.57),(0.77,4.07),(0,0.88),'Cabinet')
    B.box('F1.kit.top',(7.97,8.60),(0.75,4.10),(0.88,0.92),'Counter')
    B.cyl('F1.kit.sink',8.32,2.56,0.90,0.925,0.18,'Metal')
    B.box('F1.kit.ap',(8.57,8.60),(2.89,3.44),(0.06,0.86),'Appliance')      # dishwasher front
    B.box('F1.kit.up',(7.97,8.32),(0.95,4.05),(1.55,2.25),'Cabinet')
    for i,(x0,x1) in enumerate([(9.57,10.19),(10.21,10.82)]):
        B.box(f'F1.kit.tall{i}',(x0,x1),(0.42,1.02),(0,2.20),'Appliance')     # tall units continue to the stair wall
    B.box('F1.kit.base2',(8.57,9.55),(0.42,1.02),(0,0.88),'Cabinet')         # street-wall run
    B.box('F1.kit.base2c',(7.99,8.57),(0.42,0.77),(0,0.88),'Cabinet')
    B.box('F1.kit.top2',(8.57,9.56),(0.40,1.06),(0.88,0.92),'Counter')
    B.box('F1.kit.top2c',(7.97,8.57),(0.40,0.75),(0.88,0.92),'Counter')
    B.box('F1.kit.up2',(7.99,9.55),(0.42,0.76),(1.55,2.25),'Cabinet')         # uppers, window centered below
    B.box('F1.isl.body',(9.74,10.74),(2.38,4.08),(0,0.88),'Cabinet')
    B.box('F1.isl.top',(9.70,10.78),(2.34,4.12),(0.88,0.93),'Counter')
    B.box('F1.hood',(9.86,10.26),(2.97,3.47),(1.75,2.05),'Metal')
    B.cyl('F1.hoodduct',10.06,3.22,2.05,2.56,0.10,'Metal')
    for i,(cx,cy) in enumerate([(9.91,3.07),(9.91,3.37),(10.21,3.07),(10.21,3.37)]):
        B.cyl(f'F1.hob{i}',cx,cy,0.932,0.94,0.10,'TVBlack')
    B.cyl('F1.stool1',9.95,4.40,0,0.65,0.17,'WoodFurn'); B.cyl('F1.stool2',10.50,4.40,0,0.65,0.17,'WoodFurn')
    # dining: long axis N-S (rotated per plan), 3+3+2 chairs
    table(B,'F1.din',11.90,13.30,0.90,3.10,0.74)
    for i,y in enumerate([1.30,2.00,2.70]):
        chair(B,f'F1.dch.w{i}',11.60,y,90); chair(B,f'F1.dch.e{i}',13.60,y,270)
    chair(B,'F1.dch.n',12.60,3.40,0); chair(B,'F1.dch.s',12.60,0.60,180)
    # living
    rug(B,'F1.liv.rug',14.3,16.6,4.5,7.1)
    sofa(B,'F1.liv.sofa',14.50,16.55,6.90,7.66,'N')
    sofa(B,'F1.liv.chaise',16.00,16.66,5.80,6.90,'E')
    B.box('F1.liv.ct',(14.95,15.70),(5.85,6.50),(0.15,0.40),'FabricBlue')
    chair(B,'F1.arm1',15.40,4.50,180,'SofaWhite'); chair(B,'F1.arm2',16.25,4.85,270,'SofaWhite')
    B.box('F1.tvb',(11.90,13.70),(7.40,7.66),(0,0.45),'Cabinet')
    B.box('F1.tv',(12.25,13.35),(7.63,7.67),(0.75,1.45),'TVBlack')
    plant(B,'F1.pl1',11.25,7.30,1.1); plant(B,'F1.pl2',11.15,0.70,0.9)
    # NOTE: the LANSI vertical-board columns around the MH and kitchen strip windows are built
    # once, with the walls, as F1.slat.mh.lo/hi and F1.slat.kit.lo/hi (see build_f1_walls).
    # An earlier eyeballed pair (F1.slat.a*/b*, x 1.71-2.79 / 8.50-9.30, z 0.08-0.92 / 1.62-3.42)
    # used to be repeated here and sat 10 mm proud of them, so the two boards z-fought and the
    # column read as a doubled, stepped panel that did not line up with the opening. Removed --
    # the wall-side pair is the one measured off the plan (openings 1.640-2.739 and 8.340-9.440,
    # sill 0.74, head 1.25), so the boards split exactly at the glass.
    # entrance canopy -- plan outline x 3.936..7.148 running out to y -1.151, posts
    # x 4.236..4.351 / 6.731..6.848 at y -0.782..-0.667; LANSI gives top 2.912 at the
    # wall falling to 2.472 at the tip (underside 2.703 -> 2.263, deck 209 mm).
    # Re-measured off julkisivut.pdf + 1krs_pohja50_1.pdf.  The old block was a thin flat slab
    # on two stub posts and got three things wrong:
    #   * slope -- it fell 0.440 over the projection; ETELA and POHJOINEN both draw the top as
    #     one plane z = 2.913 + 0.3325*y, i.e. exactly 1:3, the same pitch as the two main
    #     roofs.  Top at the wall 2.912 was right, the tip was 58 mm low.
    #   * thickness -- 0.209 vs the 0.274 the ETELA layer lines give (0.077/0.077/0.120).  The
    #     "underside 2.703" in the old comment was read off the SNOW-GUARD bar, not the soffit;
    #     the soffit plane is z = 2.639 + 0.3325*y, 64 mm lower.
    #   * the posts -- the plan draws 115 mm fins projecting 837 mm from the wall (y +0.055 to
    #     -0.782), not 115 mm square stubs, and a beam under the soffit that was missing
    #     entirely (its bottom edge at z 2.181 is the heaviest line in the whole detail).
    # The roof field including the verge is x 3.896..7.191 out to y -1.224; the soffit is the
    # slightly smaller 3.936..7.148 / -1.151, which is what the old outline actually was.
    CY0,CY1 = 0.05,-1.224
    def cz(y): return 2.913+0.3325*y                     # canopy TOP plane, 1:3
    B.roofquad('F1.canopy',[(3.896,CY0,cz(CY0)),(7.191,CY0,cz(CY0)),
                            (7.191,CY1,cz(CY1)),(3.896,CY1,cz(CY1))],0.274,'Roof')
    # Runs the full soffit width and is carried by the braces, rather than stopping dead on
    # their outer faces -- ending at 4.236/6.848 put its end caps on exactly those planes.
    B.box('F1.cbeam',(3.936,7.148),(-0.801,-0.685),(2.181,2.389),'White')   # 48x198 under the soffit
    # The two 115 mm members are RAKING KNEE BRACES, not the wall fins this used to build.
    # ETELA draws them in profile as a pair of parallel lines 120 mm apart in y, running
    # (-0.170,0.798)->(-0.702,2.407) and (-0.290,0.798)->(-0.812,2.368): dy/dz = -1/3, i.e.
    # 1:3 again, the same ratio as the canopy but the other way up, at 71.57 deg.  0.798 is
    # only the terrace rail clip line, so both continue below it -- extended to the wall face
    # they land at z 0.288 and -0.072, well clear of the deck.  Nothing reaches the ground:
    # there is not one vertical segment out at the tip on any elevation, so the canopy really
    # is hung off the wall on these two braces, which is what the profile shows.
    # In plan the pair sits at x 4.236..4.351 and 6.731..6.848 -- the same footprint the fins
    # had, which is why it read as a plausible flat panel and stayed wrong.
    for i,(px0,px1) in enumerate([(4.236,4.351),(6.731,6.848)]):
        B.prism(f'F1.cbrace{i}',px0,px1,
                [(0.0,0.288),(-0.704,2.400),(-0.824,2.400),(0.0,-0.072)],'White',axis='x')
    # standing seams at the same 0.55 m pitch as the main roofs -- it is the same sheet, and
    # without them the canopy read as a plain slab beside a seamed roof.
    k=0; rx=3.936+0.275
    while rx<7.148:
        B.roofquad(f'F1.crib{k}',[(rx-0.012,CY0,cz(CY0)+0.02),(rx+0.012,CY0,cz(CY0)+0.02),
                                  (rx+0.012,CY1,cz(CY1)+0.02),(rx-0.012,CY1,cz(CY1)+0.02)],0.018,'Roof')
        rx+=0.55; k+=1
    B.box('F1.cflash',(3.896,7.191),(-0.010,0.050),(2.912,3.046),'White')   # upstand at the wall
    # Eaves gutter along the canopy's low edge, and the downpipe off its LEFT end -- left as
    # seen standing outside looking at the door, i.e. the low-x end (owner; missing entirely
    # before).  The canopy is hung off the wall with no posts, so the pipe cannot simply run
    # down beside it: it drops clear of the soffit first (the soffit at the verge is 2.232, so
    # the neck has to start below that or it would pass through the slab), necks back to the
    # cladding, and runs down the wall on clips.  x 3.966 keeps it inboard of the verge and
    # clear of the left knee brace at 4.236.
    CGY,CGZ=eaves_gutter(B,'F1.cgut',3.896,7.191,CY1,-1,cz(CY1),pitch=0.78)
    CPX=3.966
    B.cyl('F1.cdp.outlet',CPX,CGY,2.130,CGZ-GUT_R+0.03,DP_R*0.78,'Metal',10)
    B.tube3('F1.cdp.neck',[(CPX,CGY,2.130),(CPX,-0.050,1.430)],DP_R,'Metal',10)
    B.cyl('F1.cdp.fall',CPX,-0.050,0.097,1.430,DP_R,'Metal',10)
    for zc in (0.42,1.08):
        B.box(f'F1.cdp.clip{int(zc*100)}',(CPX-DP_R-0.013,CPX+DP_R+0.013),
              (-0.050-DP_R-0.011,-0.050+DP_R+0.011),(zc-0.011,zc+0.011),'Metal')
    # raked round shoe, stopping above the grate rather than running into the ground
    B.tube3('F1.cdp.shoe',[(CPX,-0.050,0.097),(CPX,-0.280,-0.063)],DP_R,'Metal',10)
    rainwell(B,'F1.cdp.well',CPX,-0.280,-0.193)

    # snow guard on the canopy: bar 4.023..7.025 at z 2.668..2.703, brackets at each end.
    # The roof top under it is cz(-0.833) = 2.636, so the bar stands ~35 mm clear -- this is
    # the detail that fixes the standoff for the two main roofs as well.
    B.box('F1.csnow.bar',(4.023,7.025),(-0.816,-0.781),(2.668,2.703),'Metal')
    for i,(bx0,bx1) in enumerate([(3.988,4.052),(7.001,7.061)]):
        B.box(f'F1.csnow.br{i}',(bx0,bx1),(-0.833,-0.784),(2.636,2.735),'Metal')
    B.floor='terassi'
    # =====================================================================
    # TERRACE -- re-measured end to end off 1krs_pohja50_1.pdf (x,y) and the
    # LANSI elevation of julkisivut.pdf (z).  Levels:
    #     -0.193  deck top          (193 mm below the 1.krs floor)
    #     -0.348  strip fascia underside
    #     -0.493  wide-deck fascia underside (300 mm edge beam)
    #     -0.690  finished grade west of the entrance strip (x 3.32..11.65)
    #     -3.361  basement yard, foot of the 16-riser outdoor stair
    # It is TWO decks, not one: a 1.48 m entrance strip along the west wall
    # (x 3.906..11.638) sitting two steps above grade, and the wide deck
    # (x 11.638..16.980) which also fills the roofed notch.  The previous block
    # ran the wide deck all the way to x 8.70 and sat 163 mm too high.
    # =====================================================================
    # Ground levels now come from Asemapiirustus 3.9.2009, calibrated for the first time
    # (1:200, sheet rotated 165.17 deg, rms 1.9 mm against the seven heavy footprint edges).
    # Its datum is 1.KRS +135.90 = z 0, confirmed three ways off the sheet's own building
    # levels: KELL +132.86 -> -3.04, KATOS +135.35 -> -0.55, VAR +135.85 -> -0.05.
    #   Z_GRADE -0.60  = +135.30, annotated at the house corner, both carport corners and the
    #                    drive -- four independent spot levels, all identical.  Was -0.690.
    #   Z_YARD  -3.25  = +132.65 at the deck SW corner, the one leader-anchored level that
    #                    sits at the foot of the stair.  Was -3.361.  This is the value to use
    #                    rather than the -3.10 site mean: it makes the 16-riser flight 165.6 mm
    #                    per riser, 1.3 mm off the 166.9 mm read straight off the elevation, so
    #                    the site plan and julkisivut agree instead of fighting.
    Z_DECK,Z_FASC = -0.193,-0.493
    # three equal risers from the strip down to grade.  The elevation's -0.348/-0.535 were
    # read against the old -0.690 grade and give 155/187/65 mm against the new one, which is
    # not a stair; re-spaced evenly at 136 mm.  Worth re-reading off LANSI if it matters.
    S1,S2 = Z_DECK+(Z_GRADE-Z_DECK)/3.0, Z_DECK+2.0*(Z_GRADE-Z_DECK)/3.0
    B.slab('T.strip',[(3.906,-1.480),(11.638,-1.480),(11.638,0.0),(3.906,0.0)],S1,Z_DECK,'Deck')
    B.slab('T.deck',[(11.638,-3.396),(16.980,-3.396),(16.980,3.30),(14.28,3.30),(14.28,0.0),(11.638,0.0)],
           Z_FASC,Z_DECK,'Deck')
    B.box('T.thresh',(4.886,6.197),(-0.28,0.0),(Z_DECK,-0.119),'Deck')   # front-door sill, LANSI z=-0.119
    # two step rings down to grade.  Each ring is 300 mm wider and 300 mm longer
    # than the one above it, wraps the north end of the strip and turns south
    # around the north-west corner of the wide deck (plan y -1.780 / -2.080).
    for nm,zt,zb,bands in [
        ('T.step1',S1,S2,[(3.607,3.906,-1.780,0.0),(3.906,11.338,-1.780,-1.480),
                                  (11.338,11.638,-3.097,-1.480)]),
        ('T.step2',S2,Z_GRADE,[(3.307,3.607,-2.080,0.0),(3.607,11.038,-2.080,-1.780),
                                   (11.038,11.338,-3.396,-1.780),(11.338,11.638,-3.396,-3.097)])]:
        for i,(x0,x1,y0,y1) in enumerate(bands):
            B.box(f'{nm}.{i}',(x0,x1),(y0,y1),(zb,zt),'Deck')
    # ---- railing: 9 slats, 58 mm face at 100 mm pitch, infill -0.172..0.686,
    # white top rail 0.703..0.798, 115 mm posts at the panel joints (both from plan).
    # The slats are the same horizontal louver as the under-deck skirt and they are right;
    # what was missing is the translucent corrugated sunroof sheet the owner fixed to the
    # INSIDE face of every infill panel as a wind and safety screen -- same product as the
    # pergola canopy, hence the same 'Canopy' material.  It is an added ply, not a
    # replacement: the slats still read as the outer face from the yard.  `inner` says which
    # of c0/c1 faces the deck, because the west rail is entered from +y, the south from -x.
    RAIL_B,RAIL_T = 0.703,0.798
    RAIL_ZB,RAIL_ZT,SCR_T = -0.172,0.686,0.015
    def railscreen(nm,a0,a1,c0,c1,axis='x',inner='hi',z0=RAIL_ZB,z1=RAIL_ZT):
        s0,s1 = (c1,c1+SCR_T) if inner=='hi' else (c0-SCR_T,c0)
        if axis=='x': B.box(f'{nm}.scr',(a0,a1),(s0,s1),(z0,z1),'Canopy')
        else:         B.box(f'{nm}.scr',(s0,s1),(a0,a1),(z0,z1),'Canopy')
    def railpanel(nm,a0,a1,c0,c1,axis='x',inner='hi'):
        for j in range(9):
            zt=RAIL_ZT-j*0.100
            if axis=='x': B.box(f'{nm}.s{j}',(a0,a1),(c0,c1),(zt-0.058,zt),'SlatGray')
            else:         B.box(f'{nm}.s{j}',(c0,c1),(a0,a1),(zt-0.058,zt),'SlatGray')
        railscreen(nm,a0,a1,c0,c1,axis,inner)
    def railpost(nm,a0,a1,c0,c1,axis='x'):
        if axis=='x': B.box(nm,(a0,a1),(c0,c1),(Z_DECK,RAIL_T),'White')
        else:         B.box(nm,(c0,c1),(a0,a1),(Z_DECK,RAIL_T),'White')
    WP=[(11.701,11.816),(13.400,13.517),(15.101,15.216),(16.802,16.917)]   # west rail, plan
    for i,(p0,p1) in enumerate(WP): railpost(f'T.rail.w.p{i}',p0,p1,-3.396,-3.287)
    for i in range(len(WP)-1): railpanel(f'T.rail.w.g{i}',WP[i][1],WP[i+1][0],-3.370,-3.303)
    B.box('T.rail.w.top',(11.701,16.917),(-3.396,-3.287),(RAIL_B,RAIL_T),'White')
    SP=[(-3.396,-3.281),(-1.632,-1.517),(0.067,0.183),(1.768,1.882),(3.186,3.301)]   # south rail, plan
    for i,(p0,p1) in enumerate(SP): railpost(f'T.rail.s.p{i}',p0,p1,16.871,16.980,axis='y')
    for i in range(len(SP)-1): railpanel(f'T.rail.s.g{i}',SP[i][1],SP[i+1][0],16.886,16.954,axis='y',inner='lo')
    B.box('T.rail.s.top',(16.871,16.980),(-3.396,3.301),(RAIL_B,RAIL_T),'White')
    # ---- pergola + sunroof over the wide deck.  NOT on the 2009 drawings: built later,
    # so this is the house as it stands (owner), not measured off a sheet.  Posts stand
    # on the west railing posts and carry the outer beam at 2.02; the inner beam is fixed
    # to the facade for x<14.28 and to two posts across the open notch bay, where there
    # is no wall behind it.  The clear sheet falls west 2.51 -> 2.18, which keeps it under
    # the wing roof (underside 2.976 at y=0, 2.743 at the y=-0.699 eave).
    PERG_TOP,PERG_LO,PERG_HI = 2.02,2.18,2.51
    def canopy_z(y): return (PERG_HI-0.03)+(PERG_LO-PERG_HI)*(-y)/3.45   # underside
    for i,(p0,p1) in enumerate(WP[1:]):
        B.box(f'T.perg.post{i}',(p0,p1),(-3.396,-3.287),(RAIL_T,PERG_TOP),'White')
    B.box('T.perg.beam1',(11.638,16.980),(-3.396,-3.256),(PERG_TOP,PERG_TOP+0.12),'White')
    B.box('T.perg.beam2',(11.638,16.980),(-0.20,-0.06),(2.36,2.48),'White')
    for i,(p0,p1) in enumerate([WP[0]]+WP[2:]):        # NE corner post + two across the notch
        B.box(f'T.perg.wpost{i}',(p0,p1),(-0.20,-0.06),(Z_DECK,2.36),'White')
    B.roofquad('T.perg.canopy',[(11.638,0.0,PERG_HI),(16.980,0.0,PERG_HI),
                                (16.980,-3.45,PERG_LO),(11.638,-3.45,PERG_LO)],0.03,'Canopy')
    for i in range(9):                                     # rafters carrying the sheet
        rx=11.85+i*0.625
        B.roofquad(f'T.perg.raf{i}',[(rx-0.032,0.0,canopy_z(0.0)),(rx+0.032,0.0,canopy_z(0.0)),
                                     (rx+0.032,-3.45,canopy_z(-3.45)),(rx-0.032,-3.45,canopy_z(-3.45))],
                   0.07,'White')
    # ---- north screen at the head of the wide deck.  NOT on the 2009 drawings and not a
    # wall: the owner's photographs show a white-framed panel closing only the WESTERN
    # half of the north edge -- corrugated translucent infill up to the rail line, open
    # square trellis above -- while the entrance passage along the facade (y -1.480..0.0,
    # the band T.strip occupies) stays completely open.  Every dimension here is read off
    # the photographs; the frame is set on the west railing post line so the corner post
    # is the continuation of T.rail.w.p0 rather than a second post beside it.
    NSX0,NSX1 = WP[0]                        # 11.701..11.816, the west-rail post line
    NS_Y0,NS_Y1 = -3.287,-1.480              # inner face of the west rail -> open passage
    NS_HB,NS_HT = 1.90,PERG_TOP              # head beam, tucked under T.perg.beam1
    B.box('T.nscr.post0',(NSX0,NSX1),(-3.396,NS_Y0),(RAIL_T,NS_HT),'White')
    B.box('T.nscr.post1',(NSX0,NSX1),(NS_Y1-0.115,NS_Y1),(Z_DECK,NS_HT),'White')
    NSA,NSB = NS_Y0,NS_Y1-0.115              # clear span between the corner posts
    B.box('T.nscr.head',(NSX0,NSX1),(NSA,NSB),(NS_HB,NS_HT),'White')
    B.box('T.nscr.mid', (NSX0,NSX1),(NSA,NSB),(RAIL_B,RAIL_T),'White')
    B.box('T.nscr.sill',(NSX0,NSX1),(NSA,NSB),(Z_DECK,Z_DECK+0.09),'White')
    # bottom panel: the same horizontal louver infill as the west and south rails, not the
    # single sheet of clear glazing this used to be -- the render showed a clear panel here
    # against slats on the west rail and the two should match.  Slats to the rail line, then
    # the corrugated sunroof sheet on the inside face.  Runs in y; the deck is at +x.
    NSZB = Z_DECK+0.09
    for j in range(9):
        zt=RAIL_ZT-j*0.100
        if zt-0.058<NSZB: break
        B.box(f'T.nscr.s{j}',(NSX0+0.030,NSX1-0.030),(NSA,NSB),(zt-0.058,zt),'SlatGray')
    railscreen('T.nscr',NSA,NSB,NSX0+0.030,NSX1-0.030,axis='y',inner='hi',z0=NSZB,z1=RAIL_B)
    nV=max(2,int(round((NSB-NSA)/0.180)))    # square trellis, ~180 mm mesh, 24 mm laths
    nH=max(2,int(round((NS_HB-RAIL_T)/0.180)))
    for i in range(1,nV):
        yc=NSA+(NSB-NSA)*i/nV
        B.box(f'T.nscr.lv{i}',(NSX0+0.040,NSX0+0.064),(yc-0.012,yc+0.012),(RAIL_T,NS_HB),'White')
    for j in range(1,nH):
        zc=RAIL_T+(NS_HB-RAIL_T)*j/nH
        B.box(f'T.nscr.lh{j}',(NSX0+0.064,NSX0+0.088),(NSA,NSB),(zc-0.012,zc+0.012),'White')
    # ---- outdoor stair: 15 risers of 176.7 mm at ~300 mm going, 1.20 m wide, with a
    # 1.143 m landing.  Nosing lines read straight off Asemapiirustus: 11.939, 12.236, 12.538,
    # 12.836, 13.138, 13.436, 13.738 | landing | 14.881, 15.183, 15.481, 15.779, 16.081,
    # 16.383, 16.680.  The upper flight matches what was here to 3 mm, but the LOWER flight has
    # one going fewer than the model had and its foot is at 16.680, not 16.915 -- so the flight
    # both ends 235 mm earlier and is correspondingly steeper.  15 risers, not 16: the drawn
    # nosings leave 14 treads, one riser off the top and one onto the yard.
    STAIR_X=[11.638,11.939,12.236,12.538,12.836,13.138,13.436,13.738,
             14.881,15.183,15.481,15.779,16.081,16.383,16.680]
    NTRD=len(STAIR_X)-1                                   # 14 tread boxes
    RISE=(Z_GRADE-Z_YARD)/(NTRD+1)
    def tread_z(k): return Z_GRADE-(k+1)*RISE
    def grade_x(x):
        t=min(1.0,max(0.0,(x-STAIR_X[0])/(STAIR_X[-1]-STAIR_X[0])))
        return Z_GRADE+(Z_YARD-Z_GRADE)*t
    # The flight is one solid stepped mass, but its underside now follows the ground instead
    # of being a flat 550 mm skirt hung off each tread.  The old version ran the last tread down
    # to -3.80, half a metre below the yard, and needed T.ground.stair dropped 600 mm clear of
    # it -- between them they read as two flights stacked on top of each other with the ground
    # cutting through.  UND is the smallest offset that still clears every nosing (the landing
    # puts the stepped profile out of phase with any straight rake, so it has to be measured,
    # not assumed), and the ground below is laid on the same rake 20 mm under it.
    # Each tread runs DOWN PAST the next tread's top, so the flight is one solid stepped
    # mass.  Taking the bottom from the straight ground rake instead made some treads 16 mm
    # thick -- grade_x is a line while tread_z steps, so wherever the two nearly touch the
    # box collapsed, and the flight rendered as a smooth ramp with a few step lips on it.
    # The landing was the worst of them and disappeared completely.
    def tread_zb(k): return (tread_z(k+1) if k+1<NTRD else Z_YARD)-0.06
    for k in range(NTRD):
        B.box(f'T.gstep{k}',(STAIR_X[k],STAIR_X[k+1]),(-4.596,-3.396),
              (tread_zb(k),tread_z(k)),'ConcreteF')
    # ground below the flight: parallel to the nosing line, 220 mm under it, so it clears
    # every tread instead of grazing the nosings and reading as the stair surface itself.
    NSLOPE=(tread_z(NTRD-1)-tread_z(0))/(STAIR_X[NTRD]-STAIR_X[0])
    def under_x(x): return tread_z(0)+NSLOPE*(x-STAIR_X[0])-0.22
    # ---- under-deck louver screen: 79 mm boards at 100 mm pitch, top board -0.535.
    # Each board dies on the raking stair standing in front of it -- that stepped left
    # edge is drawn on the elevation and is what pins the stair to the skirt.
    for i in range(26):
        zt=-0.535-i*0.100; zb=zt-0.079
        k=int(math.ceil((Z_GRADE-zb)/RISE-1e-6))-1
        x0=11.876 if k<0 else STAIR_X[min(k,NTRD)]
        if x0>=16.674: continue
        B.box(f'T.skirt.b{i}',(x0,16.674),(-3.396,-3.336),(zb,zt),'SlatGray')
    B.box('T.skirt.pN',(11.638,11.876),(-3.396,-3.336),(-0.856,Z_FASC),'SlatGray')   # 240 mm end posts
    B.box('T.skirt.pS',(16.674,16.914),(-3.396,-3.336),(Z_YARD,Z_FASC),'SlatGray')
    B.cyl('T.downpipe',16.965,-3.360,Z_YARD,Z_FASC,0.021,'White',8)                  # LANSI: 42 mm pipe
    for i in range(29):                                                              # south face of the deck
        zt=-0.535-i*0.100; zb=zt-0.079
        if zb<Z_YARD: break
        B.box(f'T.skirt.s{i}',(16.914,16.974),(-3.396,1.95),(zb,zt),'SlatGray')      # gap y1.95..3.30 = kellari door
    for i,px in enumerate([13.20,14.55,15.90]):
        B.box(f'T.dpost{i}',(px-0.06,px+0.06),(-3.36,-3.24),(grade_x(px),Z_FASC),'Deck')
    # basement-level yard.  Two separate falls, because one continuous plane over the whole
    # width buried every second tread: under the deck (y -3.396..0.0) the ground follows the
    # deck fall, while under the flight itself it is dropped 500 mm clear of the top nosing
    # so no tread is ever swallowed -- the landing shifts the stair out of phase with any
    # straight rake, and the smallest clearance this leaves is 72 mm at the last riser.
    # Nothing at all east of y=0: x 14.28..16.98 there is the open notch, which is decked
    # over by T.deck with the basement room underneath, so ground drawn across it ran a
    # sloping slab straight through Room_kellari_VAR2.
    # Under the deck the ground is simply the basement yard, flat.  It used to be a quad
    # raking the full 2.65 m from Z_GRADE at the deck's north end down to Z_YARD at the south,
    # which put a concrete ramp under the terrace that is on no drawing: the site plan gives
    # -3.25 at the deck SW corner and -3.10 a metre inboard, both basement level, and the
    # kellari does not reach past y=0.07 so this really is open yard.  The 2.65 m of level
    # change happens at the north end instead, where T.gwall retains it behind the skirt post.
    # It follows the whole deck footprint, notch included.  The old outline stopped dead at
    # y=0 on the reasoning that "the notch is decked over with the basement room underneath" --
    # but Room_kellari_VAR2 is an L that turns north at x 13.66, so x 14.17..16.98, y 0..3.30
    # has no room under it at all.  It is open basement yard, and K.wNW opens a door straight
    # into it at y 2.18..3.19.  Leaving it out left a black void you could see through from
    # the south, between the end of the louver skirt and the kellari wall.
    B.slab('T.ground',[(11.638,-3.396),(16.980,-3.396),(16.980,3.30),(14.17,3.30),
                       (14.17,0.0),(11.638,0.0)],Z_YARD-0.12,Z_YARD,'ConcreteF')
    B.box('T.gwall',(11.638,11.878),(-3.396,0.0),(Z_YARD,Z_GRADE),'ConcreteW')
    B.roofquad('T.ground.stair',[(11.638,-4.70,under_x(11.638)-0.02),(16.980,-4.70,under_x(16.980)-0.02),
                                 (16.980,-3.30,under_x(16.980)-0.02),(11.638,-3.30,under_x(11.638)-0.02)],
               0.12,'ConcreteF')   # runs 96 mm under the deck so its edge is not coplanar
                                   # with the treads' inner face at y -3.396
    B.slab('T.yard',[(16.980,-4.90),(17.40,-4.90),(17.40,3.40),(16.980,3.40)],Z_YARD-0.12,Z_YARD,'ConcreteF')
    # behind the carport: paved upper terrace at grade, then planted shelves stepping
    # down to the basement yard in even 534 mm lifts (same fall as the terrace stair)
    # Back garden.  The site plan shows NO terracing here at all -- plain NURMI lawn on one
    # continuous slope, -1.70 at x 9.5 falling to about -3.3 at x 17.3, with a play box and
    # shrub beds and not a single retaining wall anywhere on the plot.  The tiers are therefore
    # as-built, added after the 2009 permit set, so they are authored from the photographs and
    # sit ON the drawn slope rather than replacing it.  bg(x) is that slope.
    # The back slope runs the full depth of the plot, not just to x 17.30 where the model's
    # ground used to stop.  Asemapiirustus gives the boundary profile as a four-point polyline:
    # -0.60 at the house/carport line, -1.70 at the carport's far corner (9.50,-8.02), about
    # -3.20 where the model's old edge was, and -4.60 at the plot corner (25.00,-10.02).  It
    # steepens from 11.6% to 21% and then eases to 17%.
    # The ground stays dead flat at grade until the FIRST WALL at x 12.668 -- not the first
    # bed at 11.638.  The top bed is level with the paving, so nothing falls until there is a
    # wall to retain it.  The shelves are what
    # holds the level change, so nothing falls west of them.  It then drops through the four
    # of them to basement-yard level at the last wall, and eases off to the drawn -4.60 at the
    # plot corner (25.00,-10.02).  This overrides the sheet's -1.70 at the carport's far
    # corner (9.50,-8.02), which is a 2009 design level; the shelves are as-built.
    # Beyond the last shelf wall the ground is FLAT at basement-yard level all the way to the
    # boundary (owner) -- that is the level the kellari door at (14.0, 2.18..3.19) opens onto,
    # and it runs right round the back.  So the profile is: flat at grade from the street in
    # to the first wall, the whole 2.65 m drop taken by the four shelves, then flat again.
    # This supersedes the sheet's -4.60 at the plot corner (25.00,-10.02) and -3.70 at
    # (25.00,+9.97) -- as-built beats the 2009 design levels, same as at the carport corner.
    BGP=[(0.0,Z_GRADE),(12.668,Z_GRADE),(16.238,Z_YARD),(24.999,Z_YARD)]
    def bg(x):
        if x<=BGP[0][0]: return BGP[0][1]
        for (xa,za),(xb,zb) in zip(BGP,BGP[1:]):
            if x<=xb: return za+(zb-za)*(x-xa)/(xb-xa)
        return BGP[-1][1]
    BG0,BG1 = 9.504,24.999
    # The shelves start on the same line as the terrace's wide deck (owner), not 1.3 m short
    # of it -- so the paved upper terrace runs out to that line and the first wall stands on it.
    TX0=11.638
    # Everything behind the carport tiles on x 9.504 (the carport's own far face) rather than
    # 9.00: the paving used to start at 9.00 and so overlapped both the carport slab and the
    # west lawn strip, and the two lawn bands were then pulled back to x 11.0 to dodge it --
    # which left a 1.5 m hole at each end of the paving, above and below it in y.
    # Runs all the way to y -4.210 where TR.pave.court picks it up, so the strip beside the
    # house is paved end to end.  It used to stop at -5.30 with a lawn band filling the last
    # 1.1 m, which put a green rectangle in the middle of the paving.
    B.slab('T.back.pave',[(9.504,-8.90),(TX0,-8.90),(TX0,-4.210),(9.504,-4.210)],Z_GRADE-0.15,Z_GRADE,'Paver')
    if bg(TX0)<Z_GRADE-0.16:                       # no fill needed once the ground is flat here
        B.box('T.back.fill',(9.00,TX0),(-8.90,-5.30),(bg(TX0),Z_GRADE-0.15),'TierBrick')
    # Plot is 30.006 x 19.994 m (x -5.007..24.999, y -10.022..9.972) = 600 m2.  The back lawn
    # now runs the whole way out to the boundary instead of stopping 7.6 m short of it.
    # The lawn is laid AROUND the shelves, not across them.  It used to be one sheet from
    # x 9.504 to the boundary following bg(), which was fine while the fall was gentle -- but
    # once the shelves absorb the whole 2.65 m the ground between the first and last wall is a
    # 74% ramp, and a lawn drawn on it cuts straight through the beds and buries the planting.
    # Inside the shelf footprint the ground IS the shelves; there is no natural surface to draw.
    # It also used to start at x 9.504 and so z-fought T.back.pave over the 2.1 m they shared.
    TXW=XW3=TX0+3*1.15+1.15                            # x of the last wall's outer face
    B.slab('T.back.lawn.w1',[(9.504,-10.022),(TX0,-10.022),(TX0,-8.90),(9.504,-8.90)],
           Z_GRADE-0.12,Z_GRADE,'Grass')
    # Only as far as x 17.40, where T.lawnFar takes over.  It used to run to the boundary at
    # 24.999, which after the ground went flat at Z_YARD meant 20 m2 of grass laid twice at
    # exactly the same level -- two coincident surfaces fighting for every pixel.
    B.roofquad('T.back.slab0',[(TXW,-10.022,bg(TXW)),(17.40,-10.022,bg(17.40)),
                               (17.40,-4.210,bg(17.40)),(TXW,-4.210,bg(TXW))],0.12,'Grass')
    # Four brick-walled shelves.  The top one is level with the paving beside it (owner), and
    # the lifts are then sized so the last wall lands on the natural slope at its own x rather
    # than all four being an even split of the whole drop.
    XW=[TX0+i*1.15 for i in range(4)]
    LIFT=(Z_GRADE-bg(XW[3]+1.15))/4.0
    # Each shelf is a solid mass running from its own top DOWN PAST the top of the shelf below
    # it.  They used to stop at bg()-0.10 -- the natural ground at their own retaining wall --
    # but bg is a straight rake while the shelves step, so between one shelf's underside and
    # the next one's top there was up to 0.6 m of nothing: the beds read as cut slabs floating
    # with holes between them.  zb below lands 100 mm under the next shelf, and for the last
    # one that is exactly Z_YARD-0.10, since four LIFTs is the whole drop by construction.
    for i,x0 in enumerate(XW):
        zt=Z_GRADE-i*LIFT
        zg=Z_GRADE-(i+1)*LIFT
        # north end stops on the outdoor stair's outer string at y -4.596.  Running them to
        # -4.210 pushed 386 mm of bed straight through the flight.
        B.box(f'T.rsoil{i}',(x0,x0+1.03),(-9.98,-4.646),(zg-0.10,zt-0.12),'Soil')  # zg = next shelf top
        B.box(f'T.rwall{i}',(x0+1.03,x0+1.15),(-10.022,-4.596),(zg-0.10,zt),'TierBrick')
        # end returns: the beds run right out to the plot line and to the terrace side, so both
        # ends need a cheek or the soil just stops in mid-air.
        for e,(ya,yb) in enumerate(((-10.022,-9.902),(-4.716,-4.596))):
            B.box(f'T.rend{i}{e}',(x0,x0+1.15),(ya,yb),(zg-0.10,zt),'TierBrick')
        # planting: kept and loosened.  The old one was a 4x5 lattice of two spheres at a fixed
        # offset, which read as a moulded egg carton.  Same clump count, but every clump gets
        # its own position, size, squash and lean from a deterministic hash of (i,j), so no two
        # repeat and the row never lines up.
        for j in range(7):
            h=lambda k:(math.sin((i+1)*12.9898+(j+1)*78.233+k*37.719)*43758.5453)%1.0
            py=-9.85+ (j+0.15+0.7*h(1))*(5.10/7.0)
            px=x0+0.14+0.75*h(2)
            r=0.17+0.20*h(3)
            B.sph(f'T.rpl{i}{j}a',px,py,zt+r*(0.35+0.30*h(4)),r,'Plant' if h(5)<0.55 else 'Plant2')
            if h(6)>0.30:
                r2=r*(0.55+0.35*h(7))
                B.sph(f'T.rpl{i}{j}b',px+(h(8)-0.5)*0.55,py+(h(9)-0.5)*0.62,
                      zt+r2*(0.30+0.35*h(10)),r2,'Plant2' if h(5)<0.55 else 'Plant')
    # boundary hedge removed at the owner's request -- there is no hedge between the plots.
    # lawn on every unpaved yard surface (photos); east side slopes continuously down
    # Front lawns = plot minus the drawn paving.  The fall lives here, not on the drive:
    # along x=0 the ground goes -0.60 at y=0 to -0.80 at y=7.98 (2.5%), so lawn z falls with
    # +y only -- the south strip stays at grade (+135.30 is annotated there too).
    LN=6.276                                        # paving lobe's north edge, see TR.pave.ap
    B.roofquad('T.lawnN.a',[(-5.007,LN,yard_z(-5.007,LN)),(0.0,LN,yard_z(0.0,LN)),
                            (0.0,9.972,yard_z(0.0,9.972)),(-5.007,9.972,yard_z(-5.007,9.972))],0.12,'Grass')
    B.roofquad('T.lawnN.b',[(-5.007,-1.819,yard_z(-5.007,-1.819)),(-3.598,-1.819,yard_z(-3.598,-1.819)),
                            (-3.598,LN,yard_z(-3.598,LN)),(-5.007,LN,yard_z(-5.007,LN))],0.12,'Grass')
    # lawn on the far side of the carport, between it and the south plot line (owner + sheet)
    # West strip past the carport.  One quad from x -5.007 to 9.504 interpolated the whole way
    # and so sagged below grade over the first 5 m; it is bg() the same as the back garden,
    # which means dead flat at -0.60 out to x=0 and only then falling.  Same breakpoints, so
    # the two surfaces meet without a kink at the carport corner.
    LSX=[-5.007,0.0,4.75,9.504]
    for i in range(len(LSX)-1):
        xa,xb=LSX[i],LSX[i+1]
        B.roofquad(f'T.lawnS{i}',[(xa,-10.022,bg(xa)),(xb,-10.022,bg(xb)),
                                  (xb,-8.024,bg(xb)),(xa,-8.024,bg(xa))],0.12,'Grass')
    # west of the entrance strip the LANSI elevation draws a solid finished-grade line at
    # -0.690 running the whole way from the north corner to x 11.65, where the terrace stair
    # starts falling.  Without it the kellari wall stood exposed for half the facade.
    # T.lawnW.slab is gone.  It ran x -2.80..11.638, y -5.30..0 -- a grass sheet laid straight
    # over the driveway and the courtyard, which is why the lawn stood proud of the paving once
    # Z_GRADE moved up to -0.60.  On the sheet every square metre of that area is either paved
    # or carport; the only front grass is T.lawnN.a/b and T.lawnS.slab above.  T.pave.side is
    # gone with it -- TR.pave.court now covers x 0..11.638 out to the terrace in one piece.
    # ---- entrance bed.  NOT on the 2009 sheet: that strip is drawn as paving, with the only
    # planting a row of six 900 mm shrub symbols out in the 1.41 m verge at x -4.2..-4.3.  The
    # dark-brick raised bed with the conifers is as-built, so this is authored from the
    # photograph -- a 1.55 m deep planter against the wall, stopping clear of the entrance
    # steps at y 4.077, with three thujas and a run of low round shrubs.
    # 1.55 x 5.10, standing 2.25 m off the wall out at the lawn edge (its outer 0.20 m sits on
    # grass, past the drawn paving edge at -3.601), with paving running behind it along the
    # facade.  Its east end lands on the paving boundary at y 6.276, so the bed finishes flush
    # with the edge of the paved area rather than short of it.  It runs the full length of the
    # entrance steps but clears them in x -- the bottom tread reaches x -2.25 and the bed's
    # inner face is exactly there.
    BX0,BX1,BY0,BY1 = -3.80,-2.25,1.176,6.276
    BW,BH = 0.12,0.34
    B.box('T.bed.w',(BX0,BX0+BW),(BY0,BY1),(Z_GRADE-0.10,Z_GRADE+BH),'TierBrick')
    B.box('T.bed.s',(BX0,BX1),(BY0,BY0+BW),(Z_GRADE-0.10,Z_GRADE+BH),'TierBrick')
    B.box('T.bed.n',(BX0,BX1),(BY1-BW,BY1),(Z_GRADE-0.10,Z_GRADE+BH),'TierBrick')
    B.box('T.bed.soil',(BX0+BW,BX1),(BY0+BW,BY1-BW),(Z_GRADE-0.05,Z_GRADE+BH-0.06),'Soil')
    ZB=Z_GRADE+BH-0.06
    for i,ty in enumerate([BY0+0.45,(BY0+BY1)/2.0,BY1-0.45]):       # three thujas (photo)
        B.cyl(f'T.bthuja{i}.tr',BX0+0.80,ty,ZB,ZB+0.30,0.05,'WoodFurn')
        for k,(dz,r) in enumerate(((0.55,0.34),(1.05,0.30),(1.48,0.20))):
            B.sph(f'T.bthuja{i}.f{k}',BX0+0.80,ty,ZB+dz,r,'Plant')
    for j in range(7):                                              # low round shrubs
        h=lambda k:(math.sin((j+1)*12.9898+k*78.233)*43758.5453)%1.0
        sy=BY0+0.35+j*(BY1-BY0-0.7)/6.0+(h(1)-0.5)*0.22
        sx=BX0+0.30+0.55*h(2)
        r=0.20+0.14*h(3)
        B.sph(f'T.bshrub{j}',sx,sy,ZB+r*0.55,r,'Plant2' if h(4)<0.5 else 'Plant')
    # East strip: not one constant fall.  The site plan's four spot levels give a slope that
    # steepens as it goes -- -0.80 at x -1.7, -0.85 at 1.7 (1.5%), -1.55 at 7.8 (11.5%), -3.00
    # at the far house corner (15.8%) -- 2.20 m of fall, where the single quad had 2.67 m and
    # was up to 340 mm out in the middle.  Cross-fall across the 1.82 m width is under 80 mm,
    # so each segment stays flat in y.
    ELAWN=[(0.0,-0.824),(1.70,-0.850),(7.80,-1.550),(17.30,Z_YARD)]   # meets the flat far lawn
    for i in range(len(ELAWN)-1):
        (ex0,ez0),(ex1,ez1)=ELAWN[i],ELAWN[i+1]
        B.roofquad(f'T.lawnE.slab{i}',[(ex0,7.98,ez0),(ex1,7.98,ez1),(ex1,9.80,ez1),(ex0,9.80,ez0)],0.12,'Grass')
    B.slab('T.lawnSE2.slab',[(16.98,3.40),(17.40,3.40),(17.40,7.98),(16.98,7.98)],Z_YARD-0.12,Z_YARD,'Grass')
    # the far end of the plot, x 17.40..24.999.  Corner levels off the sheet: -4.60 at
    # (25.00,-10.02) and -3.70 at (25.00,+9.97), so it carries a cross-fall as well as the
    # main fall in x.  "NURMI" is labelled across all of it.
    B.slab('T.lawnFar',[(17.40,-10.022),(24.999,-10.022),(24.999,9.972),(17.40,9.972)],
           Z_YARD-0.12,Z_YARD,'Grass')
    # trampoline on the back lawn (photos) -- stands on the slope now, not at Z_YARD
    # Back in the garden where it belongs (owner), and now standing clear of the shelves --
    # the last wall ends at x 16.238 and the lawn beyond it runs to the boundary at 24.999,
    # so there is 8.7 m of open grass to put it on instead of the 1 m strip there used to be.
    TRX,TRY = 18.60,-7.00
    ZTR=bg(TRX)
    B.cyl('T.tramp.mat',TRX,TRY,ZTR+1.24,ZTR+1.30,1.10,'TVBlack',24)
    B.cyl('T.tramp.pad',TRX,TRY,ZTR+1.30,ZTR+1.34,1.20,'TVBlack',24)
    for k in range(4):
        ang=math.pi/4+k*math.pi/2
        B.cyl(f'T.tramp.leg{k}',TRX+0.95*math.cos(ang),TRY+0.95*math.sin(ang),ZTR,ZTR+1.30,0.030,'Metal',8)
    for k in range(6):
        ang=k*math.pi/3
        B.cyl(f'T.tramp.post{k}',TRX+1.18*math.cos(ang),TRY+1.18*math.sin(ang),ZTR+1.34,ZTR+2.58,0.020,'TVBlack',8)
    B.cyl('T.tramp.net',TRX,TRY,ZTR+1.36,ZTR+2.54,1.16,'Railing',24)
    # ---- terrace furniture: authored against a 0.00 deck, so drop the whole block
    # onto the measured deck top.  zoff is honoured by box/cyl/sph/slab (not roofquad).
    B.zoff=Z_DECK+0.03
    sofa(B,'T.sofa1',13.85,16.35,-3.10,-2.40,'S','Rattan')                     # lounge by the south edge
    sofa(B,'T.sofa2',15.65,16.35,-2.40,-1.15,'E','Rattan')
    B.box('T.ctable',(14.25,15.45),(-2.25,-1.45),(0.12,0.48),'Rattan')
    B.cyl('T.reel',9.10,-1.05,0,0.52,0.35,'WoodFurn')
    B.cyl('T.tbl',12.70,-1.70,0,0.72,0.60,'Rattan')
    for i,(cx,cy) in enumerate([(12.30,-1.00),(13.55,-1.00),(12.30,-2.45),(13.55,-2.45)]):
        chair(B,f'T.ch{i}',cx,cy,0 if cy>-1.7 else 180,'Rattan')
    for i,y0 in enumerate([0.70,1.80]):
        B.box(f'T.lounge{i}.seat',(15.35,16.15),(y0,y0+0.75),(0.15,0.32),'Rattan')
        B.box(f'T.lounge{i}.back',(15.35,15.60),(y0,y0+0.75),(0.32,0.75),'Rattan')
    plant(B,'T.pl1',14.60,0.45,1.0); plant(B,'T.pl2',15.05,-0.55,0.9)
    B.zoff=0.0
    # potted thujas flanking the front door, standing on grade beside the strip
    for i,(tx,ty) in enumerate([(1.6,-1.1),(3.3,-1.1)]):
        B.cyl(f'T.thuja{i}.pot',tx,ty,Z_GRADE,Z_GRADE+0.32,0.15,'Pot')
        B.cyl(f'T.thuja{i}.tr',tx,ty,Z_GRADE+0.32,Z_GRADE+0.64,0.05,'WoodFurn')
        B.sph(f'T.thuja{i}.fa',tx,ty,Z_GRADE+1.05,0.30,'Plant'); B.sph(f'T.thuja{i}.fb',tx,ty,Z_GRADE+1.50,0.22,'Plant')
    # north stoop, same 166.9 mm rise as the terrace stair
    # Stoop to TEKN.  The flight goes WEST (owner) -- it used to march out northwards in -x,
    # straight away from the wall.  West is -y here, so the treads step down alongside the
    # facade from the landing's y=4.05 edge, keeping the full x -1.35..0 width.  The levels
    # were wrong with it: pinned to Z_DECK at a fixed 166 mm rise, which put the bottom tread
    # at -0.691, below the ground it was meant to land on.  Three equal risers from the
    # landing down to whatever yard_z gives at the door.
    STG=yard_z(-0.7,4.7)+0.010
    B.slab('T.stoopW',[(-1.35,4.05),(0,4.05),(0,5.35),(-1.35,5.35)],Z_DECK-0.155,Z_DECK,'Deck')
    SRISE=(Z_DECK-STG)/3.0
    for i in range(3):
        zt=Z_DECK-(i+1)*SRISE
        B.box(f'T.stoopWstep{i}',(-1.35,0.0),(4.05-(i+1)*0.30,4.05-i*0.30),(zt-0.10,zt),'Deck')

# ================================================================= 2. KRS
def build_krs2(B):
    B.floor='2krs'
    B.slab('F2.slab.a',[(0.10,0.10),(8.81,0.10),(8.81,7.88),(0.10,7.88)],2.56,Z_2,'Concrete')
    B.slab('F2.slab.b',[(8.81,0.10),(10.88,0.10),(10.88,5.50),(8.81,5.50)],2.56,Z_2,'Concrete')
    B.slab('F2.slab.c',[(10.68,5.50),(10.88,5.50),(10.88,7.88),(10.68,7.88)],2.56,Z_2,'Concrete')
    e=EXT/2; z=Z_2
    # 2krs openings all share one band: absolute z 3.550..5.169 = Z_2 + 0.540..2.159
    wall_y(B,'F2.wS',0+e,0,10.98,z,H_2E,EXT,mat='WallExt2',m0=-1,m1=-1,ops=[
        W('win',1.644,2.773,0.54,2.16),                      # MH2 loft (mullion 1.912-2.014)
        W('win',4.392,5.489,0.54,2.16),                      # AULA pair (mullions 4.664-4.762
        W('win',5.623,6.724,0.54,2.16),                      #  and 6.452-6.650)
        W('win',8.343,9.472,0.54,2.16)])                     # MH3 (mullion 9.105-9.204)
    wall_x(B,'F2.wE',10.98-e,0,7.98,z,H_2,EXT,mat='WallExt2',m0=1,m1=1,
        ops=[W('win',1.53,2.25,0.90,2.35)])          # MH3 gable window: narrow + high (user)
    wall_y(B,'F2.wN',7.98-e,0,10.98,z,H_2E,EXT,mat='WallExt2',m0=1,m1=1,
        ops=[W('win',7.040,7.541,0.74,2.16)])                # KPH tall narrow strip (ITA elev)
    B.box('F2.slat.c',(7.04,7.54),(7.97,8.05),(3.01,3.748),'Slat')  # LP slat between the stacked windows
    wall_x(B,'F2.wW',0+e,0,7.98,z,H_2,EXT,mat='WallExt2',m0=-1,m1=-1,ops=[
        W('win',1.640,1.878,0.54,2.16),                      # MH2 north: narrow pane, then main pane,
        W('win',1.977,2.739,0.54,2.16),                      #  mullion 1.878-1.977 (POHJ elev)
        W('win',4.816,5.346,0.30,2.16),                      # balcony flank A  (sills hidden behind the
        W('door',5.416,6.326,0,2.16),                        # balcony door      solid parapet, kept as-is;
        W('win',6.425,6.958,0.30,2.16)])                     # balcony flank B   heads measured 2.161)
    # interior
    # interior per 2krs_pohja50 vector extraction
    wall_y(B,'F2.mh2_n',3.945,0.40,3.70,z,H_2,INT)                              # MH2 north (solid)
    wall_x(B,'F2.mh2_e',3.655,0.37,3.90,z,H_2,INT,ops=[W('door',2.88,3.68)])    # MH2 east + door from AULA
    wall_x(B,'F2.mh_se',3.655,3.99,4.77,z,H_2,INT)                              # master stub
    # master door sits in a 45-degree wall across the corner (O13 in the drawing)
    _p0=(3.70,4.77); _p1=(4.76,5.55)
    _dx,_dy=_p1[0]-_p0[0],_p1[1]-_p0[1]; _L=(_dx*_dx+_dy*_dy)**0.5
    _ux,_uy=_dx/_L,_dy/_L
    def _dseg(nm,a,b,zb,zt,t,mat2):
        nx,ny=-_uy*t/2,_ux*t/2
        q=[(_p0[0]+_ux*a+nx,_p0[1]+_uy*a+ny),(_p0[0]+_ux*b+nx,_p0[1]+_uy*b+ny),
           (_p0[0]+_ux*b-nx,_p0[1]+_uy*b-ny),(_p0[0]+_ux*a-nx,_p0[1]+_uy*a-ny)]
        B.slab(nm,q,Z_2+zb,Z_2+zt,mat2)
    _dseg('F2.mh_diag.a',0.0,0.18,0,H_2,INT,'WallInt')
    _dseg('F2.mh_diag.b',_L-0.18,_L,0,H_2,INT,'WallInt')
    _dseg('F2.mh_diag.lint',0.18,_L-0.18,2.10,H_2,INT,'WallInt')
    _dseg('F2.mh_diag.leaf',0.20,_L-0.20,0,2.08,0.04,'Door')
    wall_y(B,'F2.mh3_n',3.945,8.43,10.68,z,H_2,INT)                             # MH3 north (solid)
    wall_y(B,'F2.mh3_dr',3.945,7.43,8.43,z,H_2,INT,ops=[W('door',7.50,8.36)])   # MH3 doorway
    wall_x(B,'F2.se_w',7.385,0.37,3.90,z,H_2,INT)                               # MH3 west (solid)
    wall_y(B,'F2.vh_s',5.60,4.76,8.71,z,H_2,INT,ops=[W('door',6.88,7.76)])      # VH+KPH south, KPH door
    wall_x(B,'F2.mh_vh',4.775,5.65,7.68,z,H_2,INT,ops=[W('door',6.24,7.02)])    # MH -> walk-in VH door
    wall_x(B,'F2.vh_kph',6.265,5.65,7.68,z,H_2,INT)                             # VH/KPH divider
    wall_x(B,'F2.kph_st',8.71,5.55,7.68,z,H_2,INT)
    B.box('F2.strail1',(8.81,9.62),(5.54,5.60),(z,z+1.0),'Railing')     # guard at void S edge (W flight side)
    B.box('F2.strail2',(9.66,9.72),(5.60,7.66),(z,z+0.95),'Railing')    # divider top
    # upper half of the U-stair lives with 2krs so it shows in per-floor view
    riser=3.01/17
    B.box('F2.stW1',(9.74,10.60),(7.05,7.44),(0,7*riser),'StairWood')
    B.box('F2.stW2',(9.74,10.60),(7.44,7.66),(0,8*riser),'StairWood')
    B.box('F2.stW3',(9.20,9.74),(7.05,7.66),(0,9*riser),'StairWood')
    B.box('F2.stW4',(8.81,9.20),(7.05,7.66),(0,10*riser),'StairWood')
    for i in range(11,17):
        y1=7.05-(i-11)*0.25
        B.box(f'F2.stB{i}',(8.81,9.64),(y1-0.25,y1),(0,i*riser),'StairWood')
    R=B.room
    R('Room_2krs_MH',[(0.30,4.00),(3.60,4.00),(3.60,4.75),(4.72,5.58),(4.72,7.68),(0.30,7.68)],'Wood',z=Z_2)
    R('Room_2krs_VH',[(4.83,5.65),(6.22,5.65),(6.22,7.68),(4.83,7.68)],'Wood',z=Z_2)
    R('Room_2krs_KPH',[(6.31,5.65),(8.66,5.65),(8.66,7.68),(6.31,7.68)],'Tile',z=Z_2)
    R('Room_2krs_AULA',[(3.70,0.30),(7.34,0.30),(7.34,3.90),(10.68,3.90),(10.68,5.50),(4.80,5.50),(3.70,4.68)],'Wood',z=Z_2)
    R('Room_2krs_MH2',[(0.30,0.30),(3.60,0.30),(3.60,3.90),(0.30,3.90)],'Wood',z=Z_2)
    R('Room_2krs_MH3',[(7.44,0.30),(10.68,0.30),(10.68,3.90),(7.44,3.90)],'Wood',z=Z_2)
    # balcony: dark cantilevered box + white louver railing (photo)
    # everything that meets the facade is pushed 30 mm INTO the wall, never flush with x=0,
    # otherwise the coplanar faces z-fight into white hairlines along the corner.
    B.slab('F2.balc',[(-1.21,3.99),(0.03,3.99),(0.03,7.78),(-1.21,7.78)],Z_2-0.30,Z_2-0.02,'DarkWood')
    B.slab('F2.balcT',[(-1.19,4.01),(0.03,4.01),(0.03,7.76),(-1.19,7.76)],Z_2-0.02,Z_2+0.02,'Deck')
    for nm,(x0,x1,y0,y1) in {'w':(-1.21,-1.13,3.99,7.78),'s':(-1.21,0.03,3.99,4.07),'n':(-1.21,0.03,7.70,7.78)}.items():
        B.box(f'F2.brail.{nm}.top',(x0,x1),(y0,y1),(Z_2+0.96,Z_2+1.06),'White')
        for j,(z0,z1) in enumerate([(0.10,0.185),(0.225,0.31),(0.35,0.435),(0.475,0.56),(0.60,0.685),(0.725,0.81)]):
            B.box(f'F2.brail.{nm}.sl{j}',(x0,x1),(y0,y1),(Z_2+z0,Z_2+z1),'White')
    B.zoff=Z_2
    chair(B,'F2.bal.ch1',-0.62,4.75,180,'DeckRail'); chair(B,'F2.bal.ch2',-0.62,6.90,0,'DeckRail')
    B.cyl('F2.bal.tbl',-0.55,5.85,0,0.55,0.25,'DeckRail')
    plant(B,'F2.bal.pl',-0.95,7.45,0.7)
    # furniture
    bed(B,'F2.mh.bed',1.90,5.65,1.60,2.00,'y')
    B.box('F2.mh.ns1',(1.45,1.85),(7.24,7.64),(0,0.45),'WoodFurn')
    B.box('F2.mh.ns2',(3.55,3.95),(7.24,7.64),(0,0.45),'WoodFurn')
    wardrobe(B,'F2.mh.ward',0.45,3.55,4.05,4.65,2.15)   # closet row on the aula wall (valaistus plan)
    B.box('F2.mh.dress',(0.36,0.91),(5.90,6.90),(0,0.90),'WoodFurn')
    rug(B,'F2.mh.rug',1.5,4.3,5.8,7.4)
    for i,y in enumerate([5.72,7.22]):
        B.box(f'F2.vh.sh{i}',(4.95,6.15),(y,y+0.42),(0,2.0),'Cabinet')
    B.box('F2.kph.vanity',(6.36,6.91),(6.00,7.30),(0,0.85),'Cabinet')
    B.cyl('F2.kph.s1',6.64,6.35,0.85,0.95,0.16,'Ceramic'); B.cyl('F2.kph.s2',6.64,6.95,0.85,0.95,0.16,'Ceramic')
    B.box('F2.kph.mirror',(6.33,6.36),(6.05,7.25),(1.1,1.9),'Glass')
    B.box('F2.kph.tray',(7.86,8.61),(6.87,7.62),(0,0.06),'Ceramic')
    B.box('F2.kph.gl1',(7.86,7.90),(6.87,7.62),(0,1.95),'Glass')
    B.box('F2.kph.gl2',(7.86,8.61),(6.87,6.91),(0,1.95),'Glass')
    B.cyl('F2.kph.shpole',8.45,7.45,0,2.05,0.02,'Metal')
    toilet(B,'F2.kph.wc',8.30,5.98,'N')
    B.box('F2.aula.console',(4.90,6.10),(0.36,0.78),(0,0.80),'WoodFurn')
    B.box('F2.aula.mirror',(5.10,5.90),(0.32,0.35),(0.9,1.8),'Glass')
    B.box('F2.aula.daybed',(6.45,7.25),(1.30,3.10),(0.15,0.45),'SofaWhite')
    plant(B,'F2.aula.pl1',4.05,0.72,0.9); plant(B,'F2.aula.pl2',10.28,4.45,1.0)
    bed(B,'F2.sw.bed',0.70,0.35,1.15,2.00,'x')          # along the south wall (plan)
    table(B,'F2.sw.desk',2.85,3.60,0.90,2.40,0.74); chair(B,'F2.sw.ch',2.45,1.85,90)
    wardrobe(B,'F2.sw.ward',0.44,1.92,3.30,3.90,2.10)   # closet row on the north wall (plan)
    rug(B,'F2.sw.rug',0.6,3.4,0.5,3.2)
    bed(B,'F2.se.bed',9.55,0.65,1.10,2.10,'y')          # against the east gable (plan)
    wardrobe(B,'F2.se.ward',8.50,10.20,3.32,3.88,2.10)  # closet row east of the door (plan)
    table(B,'F2.se.desk',8.55,9.45,0.36,0.96,0.74); chair(B,'F2.se.ch',9.00,1.30,0)
    rug(B,'F2.se.rug',7.6,10.5,0.5,3.5)
    B.zoff=0.0

# ================================================================= KATTO
# ================================================================= RAINWATER GOODS
# The gutters were plain rectangular boxes and the downpipes bare cylinders dropped from the
# fascia to a nominal point in the ground.  A box reads as a fascia BOARD, not a gutter: what
# makes a gutter legible is the round throat, the rolled bead along its outer lip and the
# brackets it hangs on -- and what makes a downpipe legible is that it comes out of the gutter,
# necks back to the wall, and ends in something.
GUT_R = 0.058      # 116 mm half-round, the common Finnish size
DP_R  = 0.045      # 90 mm fall pipe

def eaves_gutter(B,tag,x0,x1,ye,so,ztop,drop=0.045,pitch=0.90,mat='Metal'):
    """Half-round gutter hung outboard of an eave, returning (centre y, centre z).

    `ye` is the eave line, `so` is +1 when outboard is +y, `ztop` the roof deck top at the
    eave.  The trough centre sits `drop` below the deck so the sheet overhangs into the
    throat instead of meeting the gutter edge-to-edge.  Brackets start just clear of the eave
    so they do not end up inside the roof deck.
    """
    yc=ye+so*(GUT_R+0.010); zc=ztop-drop
    B.tube(f'{tag}.trough',[(x0,yc),(x1,yc)],GUT_R,zc,mat,10)
    yb=yc+so*GUT_R
    B.box(f'{tag}.bead',(x0,x1),tuple(sorted((yb-0.011,yb+0.011))),(zc+0.026,zc+0.050),mat)
    n=max(2,int(round((x1-x0)/pitch)))
    for i in range(n+1):
        bx=x0+(x1-x0)*i/n
        bx0=min(max(bx-0.005,x0),x1-0.010)
        B.box(f'{tag}.brk{i}',(bx0,bx0+0.010),
              tuple(sorted((ye+so*0.005,yc+so*(GUT_R+0.016)))),
              (zc-GUT_R-0.016,zc-GUT_R+0.001),mat)
    return yc,zc

def downpipe(B,tag,x,yp,yg,ztr,zbot,mat='Metal'):
    """Syoksytorvi: outlet under the trough, swan-neck across to the pipe line, then the fall
    pipe on clips.  `yg` is the gutter centre, `yp` the line the pipe runs down, `ztr` the
    trough underside."""
    # The neck has to cross the whole eave overhang -- 670 mm on the block -- so a fixed
    # 340 mm drop laid it over at 63 deg and it read as a diagonal strut rather than a pipe.
    # Rake it in proportion to the offset instead.
    zo=ztr-0.02; zn=zo-max(0.34,0.85*abs(yp-yg))
    B.cyl(f'{tag}.outlet',x,yg,zo-0.05,ztr+0.03,DP_R*0.78,mat,10)
    B.tube3(f'{tag}.neck',[(x,yg,zo),(x,yp,zn)],DP_R,mat,10)
    B.cyl(f'{tag}.fall',x,yp,zbot,zn,DP_R,mat,10)
    for f in (0.32,0.72):
        zc=zbot+(zn-zbot)*f
        B.box(f'{tag}.clip{int(f*100)}',(x-DP_R-0.013,x+DP_R+0.013),
              (yp-DP_R-0.011,yp+DP_R+0.011),(zc-0.011,zc+0.011),mat)

def rainwell(B,tag,x,y,zg):
    """Sadevesikaivo -- the gully every downpipe discharges into: a concrete collar set flush
    in the surface with a cast grate standing slightly proud of it."""
    B.cyl(f'{tag}.collar',x,y,zg-0.060,zg+0.016,0.215,'ConcreteDark',16)
    B.cyl(f'{tag}.grate', x,y,zg+0.012,zg+0.030,0.170,'Metal',16)

def build_roof(B):
    """Both roofs, measured off julkisivut.pdf.

    The block and the living wing carry the SAME 1:3 gable (18.44 deg) folding about
    y = 3.990, and the wing ridge sits exactly 3.000 m below the block ridge. Deck top
    at the ridge: block 7.501, wing 4.503; eaves top 5.938 / 2.940 at y = -0.699 and
    8.681; vertical deck thickness 0.197 throughout. The wing used to be modelled with
    a 1:8 fold at y = 4.68 topping out at 3.30 -- 1.2 m too low and less than half the
    pitch. Everything that lands on the roof (bands, fascias, gutters, ribs) is derived
    from mz()/wz() below rather than hard-coded, so the two stay in step.
    """
    B.floor='katto'
    yr=3.99; TH=0.197                                  # fold line, vertical deck thickness
    YS,YN=-0.699,8.681                                 # eaves, both roofs
    def mz(y): return 7.501-abs(y-yr)/3.0              # block deck TOP
    def wz(y): return 4.503-abs(y-yr)/3.0              # wing  deck TOP
    def uz(y): return wz(y)-TH                         # wing  deck UNDERSIDE
    B.roofquad('R.main.s',[(-0.45,YS,mz(YS)),(11.43,YS,mz(YS)),(11.43,yr,mz(yr)),(-0.45,yr,mz(yr))],TH,'Roof')
    B.roofquad('R.main.n',[(-0.45,yr,mz(yr)),(11.43,yr,mz(yr)),(11.43,YN,mz(YN)),(-0.45,YN,mz(YN))],TH,'Roof')
    # y-ends inset one wall thickness so the gable tucks between the now-mitered krs2
    # eave-wall corners (F2.wS/F2.wN) instead of overlapping them; still fully under the
    # roof overhang (YS -0.699 / YN 8.681), so no exposed gap.
    gy=EXT
    for nm,gx in [('w',0.125),('e',10.855)]:
        B.prism(f'R.gable.{nm}',gx-0.125,gx+0.125,
                [(gy,5.59),(7.98-gy,5.59),(7.98-gy,mz(7.98-gy)-TH),(yr,mz(yr)-TH),(gy,mz(gy)-TH)],'WallExt2',axis='x')
    # wing: flat interior ceiling at 2.60, cold roof space above, same gable over it
    B.slab('R.wing.ceil',[(11.00,0.10),(14.18,0.10),(14.18,3.40),(16.88,3.40),(16.88,7.88),(11.00,7.88)],2.60,2.66,'WallInt')
    B.roofquad('R.wing.s',[(10.98,YS,wz(YS)),(17.53,YS,wz(YS)),(17.53,yr,wz(yr)),(10.98,yr,wz(yr))],TH,'Roof')
    B.roofquad('R.wing.n',[(10.98,yr,wz(yr)),(17.53,yr,wz(yr)),(17.53,YN,wz(YN)),(10.98,YN,wz(YN))],TH,'Roof')
    # wall bands carrying the wing roof up to its underside (ETELA is the clad gable end)
    B.prism('R.band.e',16.68,16.98,
            [(3.30,2.60),(7.98,2.60),(7.98,uz(7.98)),(yr,uz(yr)),(3.30,uz(3.30))],'WallExt',axis='x')
    # din and notch carry the cladding from the wall head at 2.60 up to the wing roof, and
    # both used to be set out on their own numbers rather than on the wall they sit on:
    # din at x 14.10..14.40 against a wall at 13.98..14.28, notch at y 3.15..3.45 against a
    # wall at 3.30..3.60.  Each therefore oversailed its wall by 120-150 mm and the head of
    # the wall read as a shelf -- and both of these are the faces you see from inside the
    # terrace, which is why it showed there and nowhere else.  Now flush with their walls.
    B.prism('R.band.din',13.98,14.28,
            [(0.0,2.60),(3.48,2.60),(3.48,uz(3.48)),(0.0,uz(0.0))],'WallExt',axis='x')
    B.prism('R.band.notch',14.28,16.98,
            [(3.30,2.60),(3.60,2.60),(3.60,uz(3.60)),(3.30,uz(3.30))],'WallExt',axis='x')
    B.prism('R.band.n',10.98,16.98,
            [(7.68,2.60),(7.98,2.60),(7.98,uz(7.98)),(7.68,uz(7.68))],'WallExt',axis='x')
    B.prism('R.band.s',10.98,14.28,
            [(0.0,2.60),(0.30,2.60),(0.30,uz(0.30)),(0.0,uz(0.0))],'WallExt',axis='x')
    B.box('R.fascia.s',(10.98,17.53),(YS,YS+0.06),(uz(YS)-0.14,uz(YS)),'WallExt')
    B.box('R.fascia.n',(10.98,17.53),(YN-0.06,YN),(uz(YN)-0.14,uz(YN)),'WallExt')
    B.prism('R.fascia.e.s',17.47,17.53,                       # raked barge board, ETELA gable
            [(YS,uz(YS)-0.14),(yr,uz(yr)-0.14),(yr,uz(yr)),(YS,uz(YS))],'WallExt',axis='x')
    B.prism('R.fascia.e.n',17.47,17.53,
            [(yr,uz(yr)-0.14),(YN,uz(YN)-0.14),(YN,uz(YN)),(yr,uz(yr))],'WallExt',axis='x')
    B.box('R.band.blk',(10.84,10.975),(0.0,7.98),(2.56,3.02),'WallInt')
    B.cyl('R.chimney',11.23,5.90,2.55,8.24,0.16,'TVBlack')     # round black steel flue
    B.cyl('R.chimcap',11.23,5.90,8.24,8.34,0.24,'TVBlack')
    # standing-seam ribs (julkisivut: seams every ~0.55 m)
    def ribs(tag,x0,x1,zf,ya,yb):
        k=0; x=x0
        while x<x1:
            B.roofquad(f'R.rib{tag}S{k}',[(x-0.012,ya,zf(ya)+0.02),(x+0.012,ya,zf(ya)+0.02),
                                          (x+0.012,yr,zf(yr)+0.02),(x-0.012,yr,zf(yr)+0.02)],0.018,'Roof')
            B.roofquad(f'R.rib{tag}N{k}',[(x-0.012,yr,zf(yr)+0.02),(x+0.012,yr,zf(yr)+0.02),
                                          (x+0.012,yb,zf(yb)+0.02),(x-0.012,yb,zf(yb)+0.02)],0.018,'Roof')
            x+=0.55; k+=1
    ribs('',   -0.17,11.42,mz,YS+0.02,YN-0.02)
    ribs('W',  11.25,17.52,wz,YS+0.02,YN-0.02)
    # gutters + downpipes (Metal): outboard of the deck edge, straddling the eaves line
    eaves_gutter(B,'R.gut.s' ,-0.45,11.43,YS,-1,mz(YS))
    eaves_gutter(B,'R.gut.n' ,-0.45,11.43,YN,+1,mz(YN))
    eaves_gutter(B,'R.gut.ws',11.43,17.53,YS,-1,wz(YS),drop=0.075)
    eaves_gutter(B,'R.gut.wn',11.43,17.53,YN,+1,wz(YN),drop=0.075)
    # ---- snow guards (lumieste).  Drawn on the y=-0.699 slope of BOTH roofs and nowhere else:
    # ITA shows a roof ladder on the block's far slope and nothing at all on the wing's, so
    # only these two runs exist.  One rail of ~32 mm section, not two, on a triangular bracket
    # at 3.0 m centres, standing 0.417 m in y inboard of the eave -- the same detail as the
    # entrance canopy, which is where the standoff is legible in section.  The sheet draws the
    # roof outer line 74 mm above mz()/wz() (roofing + battens, which the model does not build),
    # so relative to the model's deck top the rail centre lands at mz+0.110 and the bracket
    # apex at mz+0.148.  Keeping the drawing's absolute z is what puts the bar clear of the
    # roof instead of buried in it.
    GY0,GY1,GYA = -0.331,-0.132,-0.282       # bracket foot, tail toe, rail/apex
    # x ranges clamped to each roof's own extent.  The block run was drawn -0.517..11.481 off
    # the drawing, but the block deck stops at 11.43 and the wing deck beyond it is 3 m lower,
    # so the last 50 mm and its bracket hung in mid-air over the gap.
    for tag,zf,x0,x1,bxs,dz in (('m',mz,-0.450,11.430,[-0.440,2.482,5.480,8.482,11.400],0.110),
                                ('w',wz,11.100,17.125,[11.095,14.113,17.120],0.106)):
        zc=zf(GYA)+dz
        B.box(f'R.snow.{tag}.bar',(x0,x1),(GYA-0.016,GYA+0.016),(zc-0.016,zc+0.016),'Metal')
        for k,bx in enumerate(bxs):
            b0,b1=max(x0,bx-0.030),min(x1,bx+0.030)
            if b1-b0<0.010: continue
            B.prism(f'R.snow.{tag}.br{k}',b0,b1,
                    [(GY0,zf(GY0)),(GYA,zf(GYA)+dz+0.038),(GY1,zf(GY1))],'Metal',axis='x')
    # Downpipes and their wells.  Ground levels are measured off the built terrain rather than
    # carried as one nominal figure per pipe: the yard falls 2.4 m along this house, and p4, p5
    # and p6 all used to stop in mid-air -- p5 by 3.1 m.  Each pipe now dies 60 mm above the
    # grate of its own well.
    GS,GN=YS-(GUT_R+0.010),YN+(GUT_R+0.010)            # gutter centre lines
    for nm,(px,py,yg,ztr,grd) in {
            'p1':(0.30,-0.095,GS,mz(YS)-0.045-GUT_R,-0.590),
            'p2':(10.55,-0.095,GS,mz(YS)-0.045-GUT_R,-0.193),
            'p3':(0.30, 8.075,GN,mz(YN)-0.045-GUT_R,-0.829),
            'p4':(10.55, 8.075,GN,mz(YN)-0.045-GUT_R,-2.042),
            'p5':(17.20,-0.38,GS,wz(YS)-0.075-GUT_R,-3.250),
            'p6':(17.20, 8.30,GN,wz(YN)-0.075-GUT_R,-3.232)}.items():
        downpipe(B,f'R.pipe.{nm}',px,py,yg,ztr,grd+0.290)
        # The pipe hugs the cladding, so its well has to stand off the wall -- and the shoe that
        # turns the water out into it must be a raked ROUND elbow that stops ABOVE the grate
        # (owner), not a blade running into the ground.  Grate top is grd+0.030.
        wy=py+(0.29 if py>4.0 else -0.29)
        B.tube3(f'R.pipe.{nm}.shoe',[(px,py,grd+0.290),(px,wy,grd+0.130)],DP_R,'Metal',10)
        rainwell(B,f'R.well.{nm}',px,wy,grd)

# ================================================================= AUTOKATOS/TR
def build_katos(B):
    B.floor='katos'
    # Carport west of the house across the driveway; open gable mouth faces the
    # street (north), ridge front-to-back, boat inside (street-view photo)
    # Footprint straight off Asemapiirustus: x 0.000..9.504, y -8.024..-4.210, VAR partition
    # at x 6.000.  The old block was the right 9.50 x 3.81 size but sat 0.50 m and 0.885 m off,
    # which put it through the drawn driveway and left no room for the south lawn.
    X0,X1 = 0.000,9.504
    Y0,Y1 = -8.024,-4.210
    YR=(Y0+Y1)/2.0               # ridge, midway between the eaves
    zf=-0.55; hw=3.45            # bay slab +135.35 (asema: KATOS); walls reach the roof underside
    # The base is a grey concrete plinth, like the house's (owner) -- NOT the timber cladding
    # skirt the house walls get.  That skirt was the wrong answer here twice over: the carport
    # is clad to a plinth rather than to the ground, and the skirt boxes ran through TR.slab
    # with their top faces coplanar with it at z -0.55, which is the z-fighting you could see
    # flickering along the bottom of every wall.  Plinth instead: 20 mm proud of the cladding,
    # from below grade up to 120 mm above the slab, so the boards die onto it.
    PL0,PL1 = Z_GRADE-0.20, zf+0.20   # 250 mm of grey above grade, matching the house
    Xv=6.000                     # VAR storage = rear 3.5 m (asema)
    # 154 mm deep, not 120: the wall is 120 and its white inner liner sits in the next 22 mm,
    # so a 120 mm plinth ended its inner face on exactly the same plane as the wall's and left
    # the liner sticking out of it.  Two coplanar pairs over the 200 mm they overlap in z --
    # still flickering along the base after the timber skirt was taken out.  154 swallows both.
    # The three legs BUTT, they do not overlap.  Running each the full length of its side made
    # them share a 154 mm square at both corners with identical top and bottom planes -- two
    # coplanar faces of the same material fighting for the same pixels, which is the flicker
    # that survived every other fix.  Easy to miss: a check that compares the plinth against
    # everything else will not catch the plinth against itself.
    B.box('TR.plinth.w',(X0-0.020,X1+0.020),(Y0-0.020,Y0+0.154),(PL0,PL1),'ConcreteW')
    B.box('TR.plinth.s',(X1-0.154,X1+0.020),(Y0+0.154,Y1+0.020),(PL0,PL1),'ConcreteW')
    B.box('TR.plinth.e',(Xv-0.020,X1-0.154),(Y1-0.154,Y1+0.020),(PL0,PL1),'ConcreteW')
    B.slab('TR.slab',[(X0+0.06,Y0),(X1,Y0),(X1,Y1),(X0+0.06,Y1)],zf-0.15,zf,'Concrete')  # clear of the apron (no z-fight)
    # Stops 142 mm in from the outer faces on all four sides: 120 of wall plus the 22 mm white
    # liner _wall puts on the room side.  It used to run right out to the outer faces, so the
    # slab's four side planes were the walls' outer planes and facing the same way -- five
    # coplanar pairs up to 1.9 m2 fighting inside the store.  Insetting only the 120 mm wall
    # thickness just moved the problem onto the liners, which is what the check then caught.
    VF=0.142
    B.slab('TR.varfloor',[(Xv+VF,Y0+VF),(X1-VF,Y0+VF),(X1-VF,Y1-VF),(Xv+VF,Y1-VF)],
           zf,-0.05,'Concrete')                                     # VAR floor +135.85 (asema: TR)
    # gray brick paving sloping per asema: street +135.10 -> carport/entry +135.30..35
    # Front paving.  The sheet draws it as ONE unbroken 0.72 pt polyline with three R 1.20 m
    # fillets -- there is no hatch or fill anywhere on the drawing, so the fillets are what
    # identify it.  Vertices: (0,5.276) (-3.601,5.276) (-3.598,-1.819) [fillet] (-5.303,-3.024)
    # [fillet] (-6.500,-1.826) down to (-6.500,-9.227) [fillet] (-5.302,-8.025) (0,-8.023),
    # closing on the buildings.  The lobe x -3.60..0, y -1.82..5.28 carries the "1AP +135.30"
    # label = the outdoor parking space; the courtyard between house and carport carries
    # "HARVA KIVETYS TMS".  Levels: -0.60 at x=0 falling to -0.69 at the plot line and flat
    # across the verge, i.e. 1.8% -- the old ramp had it at -0.78, 130 mm too low.
    # Cut at the plot line x = -5.007, not carried out to the x = -6.500 street edge: the
    # drawn outline runs on across the verge to the kerb, but that strip is not this plot.
    # Three pieces that tile exactly.  The previous pair left a triangular hole -- the parking
    # lobe's south edge is straight at y=-1.819 while the throat's north edge was one slanted
    # line to the plot corner, so 3.6 x 0.84 m between them had no surface at all.  The
    # throat's real west boundary is a R 1.20 m fillet, taken here as its own chord from
    # (-3.598,-1.819) to (-5.007,-3.023).  The lobe follows yard_z now: flat at Z_GRADE it
    # stood up to 142 mm proud of the lawn beside it at the north end.
    # The lobe runs out to y 6.276 so the bed still finishes flush on the paving edge after
    # being moved another metre east.
    def pz(x,y): return yard_z(x,y)+0.010
    APN=6.276
    # split at y=0: yard_z has a kink there (it is flat for y<=0 and falls 2.5% above it), so
    # one quad spanning it interpolated straight through and sat 35 mm low along y=0 -- both a
    # shading warp and a real step against TR.pave.court, which is flat at grade.
    for i,(ya,yb) in enumerate(((-1.819,0.0),(0.0,APN))):
        B.roofquad(f'TR.pave.ap{i}',[(-3.598,ya,pz(-3.598,ya)),(0.0,ya,pz(0.0,ya)),
                                     (0.0,yb,pz(0.0,yb)),(-3.598,yb,pz(-3.598,yb))],0.06,'Paver')
    B.roofquad('TR.pave.drive',[(-3.598,-8.023,pz(-3.598,-8.023)),(0.0,-8.023,pz(0.0,-8.023)),
                                (0.0,-1.819,pz(0.0,-1.819)),(-3.598,-1.819,pz(-3.598,-1.819))],0.06,'Paver')
    # Square to the lawn edge, not chamfered.  The sheet's R 1.20 m fillet is real, but the
    # part of it that survives the cut at the plot line is a diagonal that leaves a wedge of
    # grass biting into the drive, and the paving should just run out to the garden edge.
    B.roofquad('TR.pave.throat',[(-5.007,-8.025,pz(-5.007,-8.025)),(-3.598,-8.023,pz(-3.598,-8.023)),
                                 (-3.598,-1.819,pz(-3.598,-1.819)),(-5.007,-1.819,pz(-5.007,-1.819))],0.06,'Paver')
    B.slab('TR.pave.court',[(0.0,-4.210),(11.638,-4.210),(11.638,0.0),(0.0,0.0)],
           Z_GRADE-0.06,Z_GRADE+0.010,'Paver')
    # jate enclosure, drawn on the sheet: 1.20 x 1.05 outside, 147 mm walls, open to the north
    # onto the paving (the paving edge line has a gap exactly here).  Three bins per the photo.
    # 1.80 wide and standing further out towards the street than the sheet's 1.199 at
    # x -2.551..-1.352 (owner) -- as-built, like the other four overrides.  Depth unchanged.
    JX0,JX1,JY0,JY1 = -4.350,-2.550,-9.076,-8.022
    # Built to the photograph, not as a blockwork hut: white painted corner posts, white
    # horizontal louver screening on the three closed sides (same board and pitch as the
    # carport's bay screen) and a white mono-pitch roof falling to the back.  It was solid
    # dark timber with a dark deck, which looked nothing like it.
    JP,JHT = 0.090,1.57                                   # post size, screen head (was 1.42)
    def jroof(y): return Z_GRADE+1.78-0.16*(JY1-y)/(JY1-JY0)
    for i,(px,py) in enumerate([(JX0,JY0),(JX1-JP,JY0),(JX0,JY1-JP),(JX1-JP,JY1-JP)]):
        B.box(f'TR.jate.p{i}',(px,px+JP),(py,py+JP),(Z_GRADE,jroof(py)),'White')
    zz=Z_GRADE+0.16; k=0
    while zz<Z_GRADE+JHT:
        B.box(f'TR.jate.s{k}',(JX0,JX1),(JY0+0.02,JY0+0.07),(zz,zz+0.088),'White')      # back
        B.box(f'TR.jate.wl{k}',(JX0+0.02,JX0+0.07),(JY0,JY1),(zz,zz+0.088),'White')     # sides
        B.box(f'TR.jate.el{k}',(JX1-0.07,JX1-0.02),(JY0,JY1),(zz,zz+0.088),'White')
        zz+=0.128; k+=1
    B.roofquad('TR.jate.roof',[(JX0-0.09,JY0-0.09,jroof(JY0)),(JX1+0.09,JY0-0.09,jroof(JY0)),
                               (JX1+0.09,JY1+0.12,jroof(JY1)),(JX0-0.09,JY1+0.12,jroof(JY1))],0.05,'White')
    for i in range(3):                                    # three bins, spread over the wider bay
        bx=JX0+0.18+i*0.51
        B.box(f'TR.jate.bin{i}',(bx,bx+0.42),(JY0+0.16,JY0+0.78),(Z_GRADE,Z_GRADE+1.10),'TVBlack')
    wall_y(B,'TR.wW',Y0+0.06,X0,X1,zf,hw,0.12,mat='WallExt2',m1=-1)               # west long wall (a0 is the open bay end)
    wall_x(B,'TR.wS',X1-0.06,Y0,Y1,zf,hw,0.12,
           ops=[W('win',-7.60,-6.10,1.55,2.15)],mat='WallExt2',m0=1,m1=1)  # rear gable wall + VAR
           # window: 1.50 x 0.60, a wide low opening (owner).  It was 0.80 x 1.00, near square.
    wall_x(B,'TR.var.n',Xv+0.06,Y0,Y1,zf,hw,0.12,mat='WallExt2',t0=0.142,m1=-1)   # VAR front wall (a0 butts TR.wW = T-junction)
    # inward=-1: the VAR interior is at -y here, so the liner must go on that side.  Left to
    # the building-centre guess it landed on the driveway face and this wall read white.
    wall_y(B,'TR.var.e',Y1-0.06,Xv,X1,zf,hw,0.12,m0=1,m1=1,
           ops=[W('door',5.80,6.70,0,2.55)],mat='WallExt2',inward=-1)     # VAR door to driveway
    # open bay: corner posts + partial white louver screen at the front-east
    for i,(px,py) in enumerate([(X0+0.02,Y1-0.18),(X0+0.02,Y0+0.06),(X0+3.22,Y1-0.18)]):
        B.box(f'TR.post{i}',(px,px+0.14),(py,py+0.14),(zf,zf+3.00),'WoodFurn')
    zz=zf+0.10; i=0
    while zz<zf+2.70:
        B.box(f'TR.screen.b{i}',(X0+0.16,X0+3.20),(Y1-0.12,Y1-0.05),(zz,zz+0.09),'SlatGray'); zz+=0.128; i+=1
    B.room('Room_katos_VAR',[(Xv+0.12,Y0+0.12),(X1-0.12,Y0+0.12),(X1-0.12,Y1-0.12),(Xv+0.12,Y1-0.12)],'ConcreteF',z=-0.05)
    B.room('Room_katos_AUTOKATOS',[(X0+0.07,Y0+0.12),(Xv,Y0+0.12),(Xv,Y1-0.05),(X0+0.07,Y1-0.05)],'ConcreteF',z=zf)
    # gable roof: ridge along x at YR, eave z2.76, ridge z3.62 (under-roof faces)
    B.roofquad('TR.roof.w',[(X0-0.30,Y0-0.30,2.88),(X1+0.30,Y0-0.30,2.88),(X1+0.30,YR,3.74),(X0-0.30,YR,3.74)],0.12,'Roof')
    B.roofquad('TR.roof.e',[(X0-0.30,YR,3.74),(X1+0.30,YR,3.74),(X1+0.30,Y1+0.30,2.88),(X0-0.30,Y1+0.30,2.88)],0.12,'Roof')
    k=0; rx=X0-0.10
    while rx<X1+0.30:
        B.roofquad(f'TR.ribW{k}',[(rx-0.012,Y0-0.28,2.90),(rx+0.012,Y0-0.28,2.90),(rx+0.012,YR,3.76),(rx-0.012,YR,3.76)],0.018,'Roof')
        B.roofquad(f'TR.ribE{k}',[(rx-0.012,YR,3.76),(rx+0.012,YR,3.76),(rx+0.012,Y1+0.28,2.90),(rx-0.012,Y1+0.28,2.90)],0.018,'Roof')
        rx+=0.55; k+=1
    B.box('TR.gut.w',(X0-0.30,X1+0.30),(Y0-0.42,Y0-0.29),(2.72,2.86),'Metal')
    B.box('TR.gut.e',(X0-0.30,X1+0.30),(Y1+0.29,Y1+0.42),(2.72,2.86),'Metal')
    B.cyl('TR.pipe.a',X0-0.20,Y0-0.34,-0.70,2.72,0.04,'Metal',10)
    B.cyl('TR.pipe.b',X1+0.20,Y0-0.34,-0.62,2.72,0.04,'Metal',10)
    for nm,gx,zb in [('n',X0+0.0,2.45),('s',X1-0.06,2.88),('v',Xv+0.06,2.88)]:
        if zb<2.88: poly=[(Y0,zb),(Y1,zb),(Y1,2.88),(YR,3.62),(Y0,2.88)]
        else:       poly=[(Y0,2.88),(Y1,2.88),(YR,3.62)]
        B.prism(f'TR.gable.{nm}',gx-0.06,gx+0.06,poly,'WallExt2',axis='x')
    B.floor='katos'
    # TR.thuja0-3 removed: a row of four conifers 1.05 m off the house wall at y 4.6..7.4,
    # which are not there (owner) and are on no drawing either.  What the sheet does draw in
    # this yard is six 900 mm shrub symbols out in the 1.41 m verge at x -4.27, y 1.53..5.57
    # and two deciduous trees at about (-4.5,-0.6) and (-4.55,8.25) -- all of it at the plot
    # boundary, none of it against the house.  Not built, since it is unconfirmed on site.

# ================================================================= VALOT
def light(B,nm,x,y,z,kind='ceil'):
    if kind=='ceil':   B.cyl(nm,x,y,z-0.028,z,0.072,'LightOff',12)
    elif kind=='spot': B.cyl(nm,x,y,z-0.038,z,0.042,'LightOff',10)
    elif kind=='pend':
        B.cyl(nm+'.cord',x,y,z+0.10,z+0.52,0.008,'Metal')
        B.cyl(nm,x,y,z-0.05,z+0.10,0.115,'LightOff',14)
    elif kind=='pendb':                                   # black pendant, long cord
        B.cyl(nm+'.cord',x,y,z+0.10,z+0.94,0.006,'Metal')
        B.cyl(nm,x,y,z-0.10,z+0.10,0.13,'TVBlack',14)
    elif kind=='wall_s': B.box(nm,(x-0.05,x+0.05),(y-0.05,y),(z-0.08,z+0.08),'LightOff')
    elif kind=='wall_ny':B.box(nm,(x-0.05,x+0.05),(y,y+0.05),(z-0.08,z+0.08),'LightOff')
    elif kind=='wall_w': B.box(nm,(x-0.05,x),(y-0.05,y+0.05),(z-0.08,z+0.08),'LightOff')
    elif kind=='boll':
        B.cyl(nm+'.pole',x,y,z,z+0.72,0.034,'Metal',10)
        B.cyl(nm,x,y,z+0.72,z+0.86,0.05,'LightOff',10)

def build_lights(B):
    # 1. krs (from '1 krs valaistus' drawing)
    B.floor='1krs'; L=lambda nm,x,y,z,k='ceil': light(B,nm,x,y,z,k)
    L('Light_1krs_LH',1.40,6.60,2.30)
    # pesuhuone: 2x2 LED grid, one switch
    L('Light_1krs_PH',2.95,6.30,2.42,'spot')
    for i,(px,py) in enumerate([(3.85,6.30),(2.95,7.10),(3.85,7.10)],start=2):
        L(f'Light_1krs_PH.p{i}',px,py,2.42,'spot')
    # kodinhoitohuone: 3x2 LED grid, one switch
    L('Light_1krs_KHH',5.00,6.25,2.52,'spot')
    for i,(px,py) in enumerate([(6.20,6.25),(7.40,6.25),(5.00,7.05),(6.20,7.05),(7.40,7.05)],start=2):
        L(f'Light_1krs_KHH.p{i}',px,py,2.52,'spot')
    L('Light_1krs_VH',8.70,6.60,2.44);      L('Light_1krs_PORRAS',9.80,6.60,2.48)
    L('Light_1krs_WC',3.30,4.60,2.44);      L('Light_1krs_TEKN',1.20,4.50,2.44)
    L('Light_1krs_ET',5.60,3.20,2.48)
    L('Light_1krs_MH',2.10,2.00,2.48);      L('Light_1krs_TK',5.50,0.90,2.48)
    L('Light_1krs_VH2',6.70,0.95,2.44)
    # keittiö: 4 LED spots in series (one switch) along the worktop aisle + island pendant
    L('Light_1krs_KT',9.15,1.90,2.52,'spot')
    for i,py in enumerate((2.90,3.90,4.90),start=2): L(f'Light_1krs_KT.p{i}',9.15,py,2.52,'spot')
    L('Light_1krs_SAAREKE',10.24,2.70,2.05,'pend')   # two island pendants in series
    L('Light_1krs_SAAREKE.p2',10.24,3.75,2.05,'pend')
    L('Light_1krs_RUOKAILU',12.60,2.00,1.95,'pend')
    # olohuone: 3x2 LED grid, one switch
    L('Light_1krs_OH',13.00,5.35,2.56,'spot')
    for i,(px,py) in enumerate([(14.55,5.35),(16.10,5.35),(13.00,6.65),(14.55,6.65),(16.10,6.65)],start=2):
        L(f'Light_1krs_OH.p{i}',px,py,2.56,'spot')
    # decorative window lights over the wing glazing (ikkunavalot, one group)
    L('Light_1krs_IKKUNA',12.39,7.68,2.52,'wall_s')
    L('Light_1krs_IKKUNA.p2',15.09,7.68,2.52,'wall_s')
    for i,py in enumerate((4.65,5.85,7.05),start=3): L(f'Light_1krs_IKKUNA.p{i}',16.68,py,2.30,'wall_w')
    L('Light_1krs_IKKUNA.p6',12.60,0.30,2.35,'wall_ny')
    L('Light_ulko_etuovi_1',4.55,-0.02,2.15,'wall_s'); L('Light_ulko_etuovi_2',6.85,-0.02,2.15,'wall_s')
    L('Light_ulko_tekn',-0.02,3.85,2.15,'wall_w')
    L('Light_ulko_terassi_1',12.20,-0.02,2.30,'wall_s'); L('Light_ulko_terassi_2',13.80,-0.02,2.30,'wall_s')
    # 2. krs
    B.floor='2krs'; z2=Z_2+2.50
    L('Light_2krs_MH',2.25,6.27,z2)
    L('Light_2krs_VH',5.60,6.80,z2)
    L('Light_2krs_KPH',7.80,6.65,z2)
    # aula: 4 LED spots in series (one switch) + kattovalo by the south windows
    L('Light_2krs_AULA',4.50,4.20,z2,'spot')
    for i,(px,py) in enumerate([(6.60,4.20),(8.20,4.80),(9.70,4.80)],start=2):
        L(f'Light_2krs_AULA.p{i}',px,py,z2,'spot')
    L('Light_2krs_AULA_KATTO',5.55,1.00,z2)
    L('Light_2krs_MH2',0.85,2.20,z2); L('Light_2krs_MH2.p2',1.95,2.20,z2)
    L('Light_2krs_MH3',8.81,2.22,z2)
    L('Light_ulko_parveke',-0.02,4.45,Z_2+2.15,'wall_w')
    L('Light_ulko_parveke.p2',-0.02,7.40,Z_2+2.15,'wall_w')
    # kellari
    B.floor='kellari'; zk=Z_K+2.42
    L('Light_kellari_VAR1_1',3.00,4.00,zk)
    L('Light_kellari_VAR1_2',3.30,5.45,Z_K+1.60,'pendb')      # 3 black pendants over the billiard
    L('Light_kellari_VAR1_2.p2',2.45,5.45,Z_K+1.60,'pendb')
    L('Light_kellari_VAR1_2.p3',4.15,5.45,Z_K+1.60,'pendb')
    L('Light_kellari_WC',1.20,1.25,zk)
    L('Light_kellari_VAR2_1',12.50,4.50,zk);L('Light_kellari_VAR2_2',15.50,5.50,zk)
    # autokatos + piha
    B.floor='katos'
    L('Light_katos_1',1.60,-7.00,2.28);     L('Light_katos_2',4.20,-7.00,2.28)
    L('Light_katos_VAR',7.20,-7.00,2.30)
    L('Light_ulko_katos',6.25,-5.10,2.10,'wall_ny')
    B.floor='terassi'
    # yard lights as wall lanterns on the terrace louver skirt (photo)
    L('Light_ulko_piha_1',12.00,-3.41,-1.30,'wall_s'); L('Light_ulko_piha_2',14.20,-3.41,-1.30,'wall_s')
    L('Light_ulko_piha_3',16.40,-3.41,-1.30,'wall_s')

# ============================================================ LATTIALÄMMITYS
# Floor-heating circuits from the LVI 'Lattialämmitys' sheets (JT = jakotukki/manifold).
# One thermostat-regulated loop each; nn = circuit number on the drawing (first digit
# = manifold: 1x/2x kellari JT1+JT2, 3x/4x 1krs JT4 by the kitchen, 5x 2krs JT3).
# Patches are thin overlay prisms above the room floor finish (hidden by default).
# nn -> (kerros, rooms served, loop length m, polygon, floor z)
HEAT = {
 '11': ('kellari','VAR2 eteläosa',      61,[(13.70,3.58),(16.57,3.58),(16.57,7.57),(13.70,7.57)],Z_K),
 '12': ('kellari','VAR2 pohjoisosa',    69,[(10.94,0.41),(13.62,0.41),(13.62,7.57),(10.94,7.57)],Z_K),
 '21': ('kellari','VAR1 länsikaista',   56,[(0.41,0.41),(10.67,0.41),(10.67,2.18),(0.41,2.18)],Z_K),
 '22': ('kellari','VAR1',               62,[(0.41,2.22),(10.67,2.22),(10.67,3.97),(0.41,3.97)],Z_K),
 '23': ('kellari','VAR1',               67,[(0.41,4.01),(10.67,4.01),(10.67,5.76),(0.41,5.76)],Z_K),
 '24': ('kellari','VAR1 itäkaista',     74,[(0.41,5.80),(10.67,5.80),(10.67,7.57),(0.41,7.57)],Z_K),
 '31': ('1krs','LH+PH',                 35,[(0.30,5.50),(4.39,5.50),(4.39,7.68),(0.30,7.68)],0.0),
 '32': ('1krs','KHH+VH',                56,[(4.49,5.65),(9.59,5.65),(9.59,7.68),(4.49,7.68)],0.0),
 '33': ('1krs','ET+TK+VH2+WC+TEKN',     70,[(0.30,3.95),(3.80,3.95),(3.80,0.30),(7.87,0.30),(7.87,5.40),(0.30,5.40)],0.0),
 '34': ('1krs','MH',                    57,[(0.30,0.30),(3.70,0.30),(3.70,3.85),(0.30,3.85)],0.0),
 '41': ('1krs','KT',                    42,[(7.97,0.30),(10.92,0.30),(10.92,5.52),(7.97,5.52)],0.0),
 '42': ('1krs','RUOKAILU',              55,[(10.96,0.30),(14.06,0.30),(14.06,3.43),(10.96,3.43)],0.0),
 '43': ('1krs','OH länsiosa',           56,[(10.96,3.47),(16.68,3.47),(16.68,5.48),(10.96,5.48)],0.0),
 '44': ('1krs','OH itäosa',             48,[(10.96,5.52),(16.68,5.52),(16.68,7.68),(10.96,7.68)],0.0),
 '51': ('2krs','MH+VH',                 64,[(0.30,4.00),(3.60,4.00),(3.60,4.75),(4.72,5.58),(4.72,5.65),(6.22,5.65),(6.22,7.68),(0.30,7.68)],Z_2),
 '52': ('2krs','MH2',                   52,[(0.30,0.30),(3.60,0.30),(3.60,3.90),(0.30,3.90)],Z_2),
 '53': ('2krs','AULA',                  61,[(3.70,0.30),(7.34,0.30),(7.34,3.90),(10.68,3.90),(10.68,5.50),(4.80,5.50),(3.70,4.68)],Z_2),
 '54': ('2krs','MH3',                   70,[(7.44,0.30),(10.68,0.30),(10.68,3.90),(7.44,3.90)],Z_2),
 '55': ('2krs','KPH',                   22,[(6.31,5.65),(8.66,5.65),(8.66,7.68),(6.31,7.68)],Z_2),
}

def _area(poly):
    s=0.0
    for i in range(len(poly)):
        x1,y1=poly[i]; x2,y2=poly[(i+1)%len(poly)]
        s+=x1*y2-x2*y1
    return abs(s)/2

def _interval(poly,axis,c):
    """Extent of the zone at scanline c (runs along `axis`)."""
    xs=[]; n=len(poly)
    for i in range(n):
        (x1,y1),(x2,y2)=poly[i],poly[(i+1)%n]
        a1,b1,a2,b2=((y1,x1,y2,x2) if axis=='x' else (x1,y1,x2,y2))
        if (a1<=c<a2) or (a2<=c<a1):
            t=(c-a1)/(a2-a1); xs.append(b1+t*(b2-b1))
    if len(xs)<2: return None
    return min(xs),max(xs)

def _bbox(poly):
    xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
    return min(xs),max(xs),min(ys),max(ys)

def _d2(p,q): return (p[0]-q[0])**2+(p[1]-q[1])**2

def _seg_hit(a,b,c,d):
    """True if ab and cd properly cross, or overlap while collinear."""
    def cr(o,p,q): return (p[0]-o[0])*(q[1]-o[1])-(p[1]-o[1])*(q[0]-o[0])
    d1,d2,d3,d4=cr(a,b,c),cr(a,b,d),cr(c,d,a),cr(c,d,b)
    if ((d1>1e-9)!=(d2>1e-9)) and ((d3>1e-9)!=(d4>1e-9)): return True
    for i in (0,1):                                     # collinear run-back along the same line
        j=1-i
        if abs(a[i]-b[i])<1e-7 and abs(c[i]-d[i])<1e-7 and abs(a[i]-c[i])<1e-6:
            lo=max(min(a[j],b[j]),min(c[j],d[j])); hi=min(max(a[j],b[j]),max(c[j],d[j]))
            if hi-lo>1e-3: return True
    return False

def _score(route,body):
    """How badly a candidate connector clashes with the loop it belongs to."""
    n=0
    for i in range(len(route)-1):
        for j in range(len(body)-1):
            if _seg_hit(route[i],route[i+1],body[j],body[j+1]): n+=1
    return n

def _lanes(poly,axis,cc,margin):
    """Lane centrelines across the zone: [(c,a0,a1)] with the runs along `axis`."""
    cs=[p[1] for p in poly] if axis=='x' else [p[0] for p in poly]
    lo,hi=min(cs)+margin,max(cs)-margin
    if hi-lo<1e-6: return []
    n=max(2,int(round((hi-lo)/cc))+1); step=(hi-lo)/(n-1)
    out=[]
    for k in range(n):
        c=lo+k*step
        iv=_interval(poly,axis,c)
        if not iv: continue
        a0,a1=iv[0]+margin,iv[1]-margin
        if a1-a0<0.05: continue
        out.append((c,a0,a1))
    return out

def _field_path(poly,axis,cc,margin,frm,to=None):
    """Serpentine spine over one zone, entered as close to `frm` and left as close to `to`
    as the lane parity allows."""
    lanes=[]; m=margin
    for f in (1.0,0.65,0.4,0.25):                   # a small zone gets a proportionate margin
        m=margin*f; lanes=_lanes(poly,axis,cc,m)
        if len(lanes)>1: break
    if not lanes: return []
    P=(lambda a,c:(a,c)) if axis=='x' else (lambda a,c:(c,a))
    best=None
    for rev in (False,True):
        seq=lanes[::-1] if rev else lanes
        for flip0 in (False,True):
            pts=[]; flip=flip0; prev=None
            for (c,a0,a1) in seq:
                s,e=(a1,a0) if flip else (a0,a1)
                if prev is not None:
                    # U-turn on the mid-line between the two lanes, so the turn is orthogonal
                    # even where the zone edge is slanted and the lane ends do not line up.
                    pc,pe=prev; cm=(pc+c)/2
                    iv=_interval(poly,axis,cm)
                    lo,hi=(iv[0]+m,iv[1]-m) if iv else (min(pe,s),max(pe,s))
                    if lo>hi: lo=hi=(lo+hi)/2
                    # Turn orthogonally, in four steps.  Going straight from the old lane end
                    # to the clamped point on the mid-line draws a diagonal whenever the two
                    # lanes have different extents -- and where a field steps (an L from the
                    # corridor cut, or a slanted wall) that diagonal runs the width of the
                    # room.  Those are the long slashes across AULA.  Travel along the lane
                    # first, then across, then along again: every segment stays axis-aligned.
                    pe2=min(max(pe,lo),hi); s2=min(max(s,lo),hi)
                    pts+=[P(pe2,pc),P(pe2,cm),P(s2,cm),P(s2,c)]
                pts+=[P(s,c),P(e,c)]
                prev=(c,e); flip=not flip
            d=_d2(pts[0],frm)+(_d2(pts[-1],to) if to else 0.0)
            if best is None or d<best[0]: best=(d,pts)
    return best[1]

def _inpoly(poly,p):
    x,y=p; c=False; n=len(poly)
    for i in range(n):
        (x1,y1),(x2,y2)=poly[i],poly[(i+1)%n]
        if (y1>y)!=(y2>y):
            if x < x1+(y-y1)*(x2-x1)/(y2-y1): c=not c
    return c

def _outside(route,polys,skip=0):
    """How much of a candidate route strays out of the zone(s) it is supposed to serve."""
    n=0
    for i in range(skip,len(route)-1):
        a,b=route[i],route[i+1]
        for k in range(1,6):
            t=k/6.0
            if not any(_inpoly(q,(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)) for q in polys): n+=1
    return n

# --- the ring ----------------------------------------------------------------
# Leads and inter-zone connectors travel a rectangle CORR in from the zone edge. Lane ends
# stop MARGIN in, so the whole ring is clear of the serpentine. Both the ring pair
# (CORR +/- d) and the first lane pair (MARGIN +/- d) must also clear each other by the
# pipe OD once the spine is offset, which is what fixes MARGIN - CORR >= 2*d + 0.022.
MARGIN=0.29
CORR=0.10
GAP=MARGIN-CORR                                   # the ring-to-lane step

def _ring(bb):
    x0,x1,y0,y1=bb; mx=(x0+x1)/2; my=(y0+y1)/2
    return (min(x0+CORR,mx),max(x1-CORR,mx),min(y0+CORR,my),max(y1-CORR,my))

def _to_ring(p,R):
    """Nearest point on the ring rectangle's boundary."""
    rx0,rx1,ry0,ry1=R
    cx=min(max(p[0],rx0),rx1); cy=min(max(p[1],ry0),ry1)
    return min([(rx0,cy),(rx1,cy),(cx,ry0),(cx,ry1)],key=lambda q:_d2(p,q))

def _ringT(p,R):
    """Perimeter parameter of a ring point, measured from the (rx0,ry0) corner."""
    rx0,rx1,ry0,ry1=R; W=rx1-rx0; H=ry1-ry0
    s=[abs(p[1]-ry0),abs(p[0]-rx1),abs(p[1]-ry1),abs(p[0]-rx0)]
    k=s.index(min(s))
    if k==0: return p[0]-rx0
    if k==1: return W+(p[1]-ry0)
    if k==2: return W+H+(rx1-p[0])
    return 2*W+H+(ry1-p[1])

def _ringP(t,R):
    rx0,rx1,ry0,ry1=R; W=rx1-rx0; H=ry1-ry0; P=2*(W+H)
    if P<1e-9: return (rx0,ry0)
    t%=P
    if t<=W:     return (rx0+t,ry0)
    if t<=W+H:   return (rx1,ry0+t-W)
    if t<=2*W+H: return (rx1-(t-W-H),ry1)
    return (rx0,ry1-(t-2*W-H))

def _walk(a,b,R,rev):
    """Ring path from a to b inclusive, one way round (rev=0) or the other (rev=1)."""
    rx0,rx1,ry0,ry1=R; W=rx1-rx0; H=ry1-ry0; P=2*(W+H)
    if P<1e-9: return [a,b]
    ta,tb=_ringT(a,R),_ringT(b,R)
    span=((tb-ta)%P) if not rev else ((ta-tb)%P)
    out=[a]
    for c in sorted((((c0-ta)%P) if not rev else ((ta-c0)%P)) for c0 in (0.0,W,W+H,2*W+H)):
        if 1e-6<c<span-1e-6: out.append(_ringP(ta+c if not rev else ta-c,R))
    out.append(b)
    return out

def _link(pA,polyA,pB,polyB,body):
    """Connector between two zones of the same circuit: out to zone A's ring, round it to the
    side facing B, across the gap, then round B's ring to B's first lane end."""
    RA=_ring(_bbox(polyA)); RB=_ring(_bbox(polyB))
    eA=_to_ring(pA,RA); eB=_to_ring(pB,RB)
    fA=_to_ring(eB,RA); fB=_to_ring(eA,RB)          # the facing point on each ring
    best=None
    for ra in (0,1):
        wa=_walk(eA,fA,RA,ra)
        for rb in (0,1):
            r=[pA]+wa+_walk(fB,eB,RB,rb)+[pB]
            s=(10*(_score(r,body)+_selfhits(r))+_outside(r,[polyA,polyB])
               +2.0*_diag(r)+0.10*_plen(r))
            if best is None or s<best[0]: best=(s,r)
    return best[1][1:-1]

def _body(fields,cc,jp,margin):
    """The serpentines of every zone in the circuit, joined in series (no manifold leader)."""
    body=[]; prev=jp; prevpoly=None
    for i,(poly,axis) in enumerate(fields):
        nxt=None
        if i+1<len(fields):
            b2=_bbox(fields[i+1][0]); nxt=((b2[0]+b2[1])/2,(b2[2]+b2[3])/2)
        pts=_field_path(poly,axis,cc,margin,prev,nxt)
        if not pts: continue
        if body: body+=_link(body[-1],prevpoly,pts[0],poly,body)
        body+=pts; prev=pts[-1]; prevpoly=poly
    return body

def _leads(jp,p0,poly,stub=None):
    """Candidate routes from the manifold port to the first lane end, as (route, first index
    that has to stay inside the zone). The manifold usually sits in another room, so the hops
    that reach the ring are allowed out; everything after them is not.
    `stub` is this circuit's own trunk leg straight out of the bar: every circuit on a
    jakotukki leaves in its own lane, so the leads run side by side instead of on top of
    each other on the way to their rooms."""
    R=_ring(_bbox(poly)); x=_to_ring(p0,R)
    head=[jp] if stub is None else [jp,stub]
    q=head[-1]; k=len(head)-1; e=_to_ring(q,R)
    out=[]
    if stub is not None:
        # First choice: stay in this circuit's own trunk lane until it is level with the room,
        # then turn straight in. Going out to the zone's ring instead would put every circuit of
        # the manifold on the same line. The corner is still in the feed corridor, so it is
        # exempt from the inside test; these come first so they win an otherwise equal score.
        m=0 if abs(stub[0]-jp[0])>1e-9 else 1
        ln=lambda t:(q[0],t[1]) if m==0 else (t[0],q[1])
        out+=[(head+[ln(p0),p0],k+2),(head+[ln(x),x,p0],k+2)]
    for rev in (0,1):
        w=_walk(e,x,R,rev)
        out.append((head+w+[p0],k+1))
        out.append((head+[(e[0],q[1])]+w+[p0],k+2))
        out.append((head+[(q[0],e[1])]+w+[p0],k+2))
    out+=[(head+[(q[0],p0[1]),p0],k+1),(head+[(p0[0],q[1]),p0],k+1),(head+[p0],k+1)]
    return out

def _onothers(route,others):
    """How much of a candidate lead runs over ANOTHER circuit's floor.  Those loops are
    already down, and the corridor and shadow lanes exist precisely so a feed need not cross
    them -- but nothing in the score noticed when one did, which is how 51's lead came to run
    diagonally over five of 53's lanes.  The straight point-to-point candidate is always the
    shortest, so without this term it wins whenever it is not strictly outside its own zone."""
    n=0
    for i in range(len(route)-1):
        a,b=route[i],route[i+1]
        for k in range(1,6):
            t=k/6.0
            q=(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
            if any(_inpoly(g,q) for g in others): n+=1
    return n

def _slide(b,c,a):
    """Move c so that the leg b->c keeps its direction but starts from a instead of b."""
    if abs(b[1]-c[1])<1e-7: return (c[0],a[1])
    if abs(b[0]-c[0])<1e-7: return (a[0],c[1])
    return (c[0]+a[0]-b[0],c[1]+a[1]-b[1])

def _mklen(pts,mn):
    """A segment shorter than the offset distance makes the parallel curve overshoot and fold
    back. The manifold usually sits a few centimetres off the corridor, so rather than drop
    that hop (which would leave a diagonal) slide the corridor onto the manifold."""
    P=[p for i,p in enumerate(pts) if i==0 or _d2(p,pts[i-1])>1e-12]
    while len(P)>2 and math.dist(P[0],P[1])<mn:
        P[2]=_slide(P[1],P[2],P[0]); del P[1]
        P=[p for i,p in enumerate(P) if i==0 or _d2(p,P[i-1])>1e-12]
    out=[P[0]]
    for p in P[1:-1]:
        if math.dist(p,out[-1])>=mn: out.append(p)
    while len(out)>1 and math.dist(P[-1],out[-1])<mn: out.pop()
    out.append(P[-1])
    return out

def _selfhits(p):
    n=0
    for i in range(len(p)-1):
        for j in range(i+2,len(p)-1):
            if _seg_hit(p[i],p[i+1],p[j],p[j+1]): n+=1
    return n

def _diag(pts):
    """Segments that are neither horizontal nor vertical. A pipe is pulled along the joists
    and turned at right angles, so a diagonal across a room is always the wrong answer."""
    return sum(1 for i in range(len(pts)-1)
               if abs(pts[i][0]-pts[i+1][0])>1e-6 and abs(pts[i][1]-pts[i+1][1])>1e-6)

def _plen(pts):
    return sum(math.dist(pts[i],pts[i+1]) for i in range(len(pts)-1))

def _fillet(pts,R,seg=4):
    """Round every corner of the spine. Real PEX cannot turn a sharp corner anyway, and an
    arc offsets to a concentric arc, so this is also what keeps the parallel curves clean."""
    if len(pts)<3: return list(pts)
    out=[pts[0]]
    for i in range(1,len(pts)-1):
        a,b,c=pts[i-1],pts[i],pts[i+1]
        ux,uy=b[0]-a[0],b[1]-a[1]; vx,vy=c[0]-b[0],c[1]-b[1]
        lu=math.hypot(ux,uy); lv=math.hypot(vx,vy)
        if lu<1e-9 or lv<1e-9: continue
        ux,uy,vx,vy=ux/lu,uy/lu,vx/lv,vy/lv
        th=math.acos(max(-1.0,min(1.0,ux*vx+uy*vy)))
        if th<1e-4: continue                                  # straight through
        if th>3.0: out.append(b); continue                    # fold: _offset squares it off
        t=min(R*math.tan(th/2),0.45*lu,0.45*lv); r=t/math.tan(th/2)
        p0=(b[0]-ux*t,b[1]-uy*t); p1=(b[0]+vx*t,b[1]+vy*t)
        s=1.0 if ux*vy-uy*vx>0 else -1.0
        C=(p0[0]-uy*s*r,p0[1]+ux*s*r)
        a0=math.atan2(p0[1]-C[1],p0[0]-C[0]); a1=math.atan2(p1[1]-C[1],p1[0]-C[0])
        da=(a1-a0+math.pi)%(2*math.pi)-math.pi
        out.append(p0)
        for k in range(1,seg): out.append((C[0]+r*math.cos(a0+da*k/seg),C[1]+r*math.sin(a0+da*k/seg)))
        out.append(p1)
    out.append(pts[-1])
    return out

def _offset(pts,d):
    """Parallel offset of a polyline by d (left of travel), mitred at the corners."""
    P=[pts[0]]
    for p in pts[1:]:
        if abs(p[0]-P[-1][0])>1e-7 or abs(p[1]-P[-1][1])>1e-7: P.append(p)
    if len(P)<2: return list(P)
    U=[]
    for i in range(len(P)-1):
        dx,dy=P[i+1][0]-P[i][0],P[i+1][1]-P[i][1]
        L=math.hypot(dx,dy); U.append((dx/L,dy/L))
    out=[(P[0][0]-U[0][1]*d, P[0][1]+U[0][0]*d)]
    for i in range(len(U)-1):
        u,v=U[i],U[i+1]; b=P[i+1]
        Q=(b[0]-u[1]*d, b[1]+u[0]*d); R=(b[0]-v[1]*d, b[1]+v[0]*d)
        cz=u[0]*v[1]-u[1]*v[0]
        if abs(cz)<1e-9:
            out.append(Q)
            if _d2(Q,R)>1e-12: out.append(R)              # 180 deg fold: square the end off
            continue
        t=((R[0]-Q[0])*v[1]-(R[1]-Q[1])*v[0])/cz
        M=(Q[0]+u[0]*t, Q[1]+u[1]*t)
        # A shallow corner sends the mitre point off to infinity, which is what produced the
        # long spikes across the house. Past the limit, bevel the corner instead.
        if _d2(M,b)>(2.2*abs(d))**2: out+=[Q,R]
        else: out.append(M)
    out.append((P[-1][0]-U[-1][1]*d, P[-1][1]+U[-1][0]*d))
    return out

# run orientation per the LL sheets (default 'x' = along the house)
HEATAXIS={'11':'y','12':'y','41':'y','42':'y'}
# loops serving two rooms: separate serpentine per room, connected in series
HEATFIELDS={
 '31':[([(0.30,5.50),(2.39,5.50),(2.39,7.68),(0.30,7.68)],'y'),   # LH runs poikittain
      ([(2.49,5.50),(4.39,5.50),(4.39,7.68),(2.49,7.68)],'x')],   # PH runs pitkittain
 '32':[([(4.49,5.65),(7.87,5.65),(7.87,7.68),(4.49,7.68)],'x'),
      ([(7.97,5.65),(9.59,5.65),(9.59,7.68),(7.97,7.68)],'x')],
 '33':[([(2.54,3.95),(3.80,3.95),(3.80,5.40),(2.54,5.40)],'x'),   # WC only -- TEKN is the
      # plant room and carries the jakotukki itself; no floor loops in it (owner).
      ([(3.80,0.30),(7.87,0.30),(7.87,5.40),(3.80,5.40)],'x')],   # ET+TK+VH2
}
# jakotukit: first digit of the circuit -> (kerros, x, y, mount axis)
HEATJT={'1':('kellari',10.98,0.80,'y'),'2':('kellari',10.635,0.80,'y'),
        '3':('1krs',1.66,4.02,'x'),      # in TEKN on the south wall (LVI '1 KRS.pdf')
        '4':('1krs',9.35,5.72,'x'),'5':('2krs',8.15,5.68,'x')}

# A jakotukki is not a point: it is a bar carrying one supply/return port PAIR per circuit,
# about 150 mm apart. Each circuit therefore leaves from its own port, and the ports are
# ordered so the fan-out towards the rooms never has to cross itself.
PORTPITCH=0.15

# Explicit port order per jakotukki, lowest coordinate first.  The automatic order is derived
# from where each circuit turns off, which is a reasonable guess but only a guess -- the real
# bar is plumbed the way it is plumbed.  Anything listed here overrides it.
#   jt3: KHH is the second port (owner).  The derived order put it fourth, at the far end, so
#   its lead ran the whole length of the bar before turning off.
HEATPORTORDER={'3':['31','32','33','34']}

def _ports(rank):
    """Port order along the bar. `rank` gives the point on the floor each circuit is heading
    for, and the ports follow the same order: the circuit that turns off furthest to the left
    gets the leftmost port. Any other order makes two leads swap places somewhere between the
    bar and the rooms, and a swap is a crossing."""
    out={}
    for j,(kerros,x,y,axis) in HEATJT.items():
        k=0 if axis=='x' else 1
        cs=sorted([nn for nn in HEAT if nn[0]==j],key=lambda nn:(rank[nn][k],nn))
        pinned=j in HEATPORTORDER
        if pinned:                                   # the real bar; not ours to optimise
            cs=[nn for nn in HEATPORTORDER[j] if nn in cs]+[nn for nn in cs if nn not in HEATPORTORDER[j]]
        # Position order alone is not enough.  53 and 54 turn off in the right order along the
        # bar, but 54 drops away far more steeply and overtakes 53 before either reaches its
        # room -- a crossing that no amount of sorting by turn-off point can see, because it
        # depends on the angle the lead leaves at, not where it lands.  So optimise against
        # the thing we actually care about: count the crossings the order produces and swap
        # adjacent ports while that count falls.  Five circuits at most per bar, so it is a
        # handful of comparisons.
        def _cross(order):
            m=len(order); c=0
            pos={}
            for i,nn in enumerate(order):
                o=(i-(m-1)/2.0)*PORTPITCH
                pos[nn]=(x+o,y) if k==0 else (x,y+o)
            for i in range(m):
                for jj in range(i+1,m):
                    a,b=order[i],order[jj]
                    if _seg_hit(pos[a],rank[a],pos[b],rank[b]): c+=1
            return c
        bc=0 if pinned else _cross(cs)   # pinned: skip the swap search, it would undo the order
        while bc:
            for i in range(len(cs)-1):
                t=cs[:]; t[i],t[i+1]=t[i+1],t[i]
                tc=_cross(t)
                if tc<bc: cs,bc=t,tc; break
            else:
                break
        n=len(cs)
        for i,nn in enumerate(cs):
            o=(i-(n-1)/2.0)*PORTPITCH
            out[nn]=(x+o,y) if k==0 else (x,y+o)
    return out

def _clip(poly,axis,c,keep):
    """Sutherland-Hodgman half-plane clip: keep='lo' keeps the side below c."""
    k=0 if axis=='x' else 1
    ins=(lambda p:p[k]<=c+1e-9) if keep=='lo' else (lambda p:p[k]>=c-1e-9)
    out=[]
    for i in range(len(poly)):
        a,b=poly[i],poly[(i+1)%len(poly)]
        ia,ib=ins(a),ins(b)
        if ia: out.append(a)
        if ia!=ib and abs(b[k]-a[k])>1e-12:
            t=(c-a[k])/(b[k]-a[k])
            out.append((a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t))
    return out

def _bands():
    """Plan the feed corridors first, then hand each circuit the floor that is left.

    The corridor beside a jakotukki carries every circuit's supply and return, so no
    serpentine may sit in it -- but it is not a band ruled across the whole floor plate at
    full width, which is what this used to reserve.  Two things shape it:

      * it TAPERS.  Feeds peel off as they reach their circuits, so the strip only has to be
        wide enough for the ones still travelling.  Order the circuits on a side by distance
        from the manifold along the bar; the one at rank r of n carries n-r feeds.
      * it is only as LONG as the feeds run.  The last circuit on a run has nothing passing
        over it -- just its own lead turning in -- so it gives up a 600 mm stub, not its whole
        length.  Circuit 33 was losing 4.4 m2 to a corridor that only needed to cross it once.

    Returns (ax, lo, hi, axb, r0, r1): the corridor rectangle to subtract.
    """
    # Rank and side come from the REAL field geometry, not HEAT[nn][3]: where a circuit has a
    # HEATFIELDS entry the two differ, and 33's HEAT zone bbox reaches back over the manifold,
    # so it ranked as the nearest circuit and drew a full-length corridor over itself when it
    # is in fact the last one on the run.
    def rawf(nn): return HEATFIELDS.get(nn) or [(HEAT[nn][3],HEATAXIS.get(nn,'x'))]
    def rawbb(nn): return _bbox([q for g,_ in rawf(nn) for q in g])
    out={}
    for j,(kerros,x,y,axis) in HEATJT.items():
        k=0 if axis=='y' else 1     # bar along y -> the corridor is a band in x, and vice versa
        c=(x,y)[k]; ax='x' if k==0 else 'y'
        kb=1-k; cb=(x,y)[kb]; axb='x' if kb==0 else 'y'
        side={'lo':[],'hi':[]}
        for nn in sorted(HEAT):
            if nn[0]!=j: continue
            b=rawbb(nn)
            side['lo' if (b[0]+b[1],b[2]+b[3])[k]/2<c else 'hi'].append(nn)
        def near(nn):
            b=rawbb(nn)
            return 0.0 if b[2*kb]<=cb<=b[2*kb+1] else min(abs(b[2*kb]-cb),abs(b[2*kb+1]-cb))
        for s,cs in side.items():
            if not cs: continue
            order=sorted(cs,key=near); n=len(order)
            for r,nn in enumerate(order):
                w=0.16+PORTPITCH*(n-r)
                b=rawbb(nn); z0,z1=b[2*kb],b[2*kb+1]
                if r==n-1:                                   # nothing runs past it
                    if   cb<=z0: r0,r1=z0,min(z1,z0+0.60)
                    elif cb>=z1: r0,r1=max(z0,z1-0.60),z1
                    else:        r0,r1=cb-0.30,cb+0.30
                else:
                    r0,r1=z0,z1
                out[nn]=(ax,c-w,c+w,axb,r0,r1)
    return out
HEATBAND=_bands()

def _cut_corridor(p,ax,lo,hi,axb,r0,r1):
    """Zone minus the corridor rectangle, as FEW pieces as possible.

    Piece count matters more than it looks.  Every extra piece is another place the
    serpentine has to break off and hop, and in a small room the hops knot up: the corridor
    was biting the corner off the WC and leaving two 0.6-0.9 m2 slivers, which gave circuit
    33 five self-crossings in a 1.3 m wide room.  Where the bite is a corner the remainder is
    an L, and an L scanned along its own axis still gives one span per lane, so the
    serpentine runs through it in a single pass.  Only a bite out of the middle of an edge
    genuinely needs two pieces.
    """
    if ax=='x': cx0,cx1,cy0,cy1 = lo,hi,r0,r1
    else:       cx0,cx1,cy0,cy1 = r0,r1,lo,hi
    xs=sorted(set(round(q[0],6) for q in p)); ys=sorted(set(round(q[1],6) for q in p))
    if len(p)==4 and len(xs)==2 and len(ys)==2:
        x0,x1=xs; y0,y1=ys; E=1e-9
        ix0,ix1=max(x0,cx0),min(x1,cx1); iy0,iy1=max(y0,cy0),min(y1,cy1)
        if ix1-ix0<=E or iy1-iy0<=E: return [p]
        R=lambda a,b,c,d:[(a,c),(b,c),(b,d),(a,d)]
        if ix0<=x0+E and ix1>=x1-E:                       # band right across
            out=[]
            if iy0-y0>E: out.append(R(x0,x1,y0,iy0))
            if y1-iy1>E: out.append(R(x0,x1,iy1,y1))
            return [q for q in out if _area(q)>0.6]
        if iy0<=y0+E and iy1>=y1-E:
            out=[]
            if ix0-x0>E: out.append(R(x0,ix0,y0,y1))
            if x1-ix1>E: out.append(R(ix1,x1,y0,y1))
            return [q for q in out if _area(q)>0.6]
        L=ix0<=x0+E; Rt=ix1>=x1-E; B=iy0<=y0+E; T=iy1>=y1-E
        if (L or Rt) and (B or T):                        # corner bite -> single L
            if   L and B:  q=[(ix1,y0),(x1,y0),(x1,y1),(x0,y1),(x0,iy1),(ix1,iy1)]
            elif L:        q=[(x0,y0),(x1,y0),(x1,y1),(ix1,y1),(ix1,iy0),(x0,iy0)]
            elif B:        q=[(x0,y0),(ix0,y0),(ix0,iy1),(x1,iy1),(x1,y1),(x0,y1)]
            else:          q=[(x0,y0),(x1,y0),(x1,iy0),(ix0,iy0),(ix0,y1),(x0,y1)]
            return [q] if _area(q)>0.6 else []
    out=[_clip(p,axb,r0,'lo'),_clip(p,axb,r1,'hi')]       # non-rectangular zones (51, 53)
    mid=_clip(_clip(p,axb,r0,'hi'),axb,r1,'lo')
    if len(mid)>=3: out+=[_clip(mid,ax,lo,'lo'),_clip(mid,ax,hi,'hi')]
    return [q for q in out if len(q)>=3 and _area(q)>0.6]

def _fields(nn):
    f=HEATFIELDS.get(nn) or [(HEAT[nn][3],HEATAXIS.get(nn,'x'))]
    bd=HEATBAND.get(nn)
    if not bd: return f
    g=[(q,a) for p,a in f for q in _cut_corridor(p,*bd)]
    return g or f
HEATFLD0={}

# --- feed lanes past a circuit that is in the way ------------------------------
# The corridor beside the jakotukki only gets a lead as far as the near room. Where a circuit's
# room lies BEYOND another circuit of the same manifold there is no way in except across that
# circuit's floor, so the loops already there have to give up a lane for it -- which is what an
# installer does, running the far circuit's feed tight along one edge of the near room.
LANEW=0.26

def _fbb(f): return _bbox([p for g,_ in f for p in g])

def _cc_of(f,m):
    """Lane pitch that spends the circuit's drawn `lenkki` metres over its field area.

    A serpentine of pitch cc covering area A has length A/cc, so cc = A/m.  It used to be
    2*A/m -- double the pitch, therefore half the pipe -- and every one of the 19 circuits
    built to 35-66% of its design length, which is the gaps you can see between the loops.
    _area() already returns the true shoelace area, so there was nothing for the 2 to undo.

    The floor is 0.10, not 0.18: at the correct pitch several circuits genuinely want
    140-150 mm (51 wants 0.142), which 0.18 would clamp away and leave short again.
    """
    return max(0.10,min(0.50,sum(_area(g) for g,_ in f)/max(1,m)))

def _shadow():
    """Which circuits are boxed in, where their feed comes through, and what it costs the
    circuit in the way. Returns (entry point per boxed-in circuit, clips per blocker)."""
    ent={}; cut={}
    for j,(kerros,jx,jy,axis) in HEATJT.items():
        k=0 if axis=='x' else 1                     # ports vary along k, pipes leave along 1-k
        bar=(jx,jy)[1-k]
        cs=[nn for nn in sorted(HEAT) if nn[0]==j]
        R={nn:_fbb(HEATFLD0[nn]) for nn in cs}
        for a in cs:
            ra=R[a]; s=1 if (ra[2*(1-k)]+ra[2*(1-k)+1])/2>=bar else -1
            # a's own distance to the bar, measured the same way as b's below: the nearest
            # floor it has on its side, and 0 if it straddles.  Taking the bbox extreme made
            # a straddling circuit look far away -- 53 spans the bar, read as 2.00 m off it,
            # and so acquired blockers it does not have.
            near=(max(ra[2*(1-k)],bar)-bar) if s>0 else (bar-min(ra[2*(1-k)+1],bar))
            for b in cs:
                if b==a: continue
                rb=R[b]
                # Does b have floor on a's side of the bar, nearer to it than a?  Judging that
                # by b's CENTRE was wrong: 33's field runs y 0.30..5.40 straight through the
                # bar at 4.02, so its centre put it on the far side and it was never treated as
                # a blocker -- while in fact it lies between the manifold and 32, whose lead
                # then ran over seven of its loops.  Measure the edge, not the middle.
                if s>0:
                    if rb[2*(1-k)+1]<=bar+0.05: continue        # b is entirely below the bar
                    bnear=max(rb[2*(1-k)],bar)-bar
                else:
                    if rb[2*(1-k)]>=bar-0.05: continue
                    bnear=bar-min(rb[2*(1-k)+1],bar)
                # b is in the way if it reaches nearer the bar than a does: then a's feed cannot
                # drop straight out of the corridor without landing on b's loops
                if bnear>near-0.05: continue
                lo=max(ra[2*k],rb[2*k]); hi=min(ra[2*k+1],rb[2*k+1])
                if hi-lo<0.4: continue               # no shared frontage, nothing to cross
                # come in at whichever end of the shared frontage is also an end of the blocker
                dlo=abs(lo-rb[2*k]); dhi=abs(hi-rb[2*k+1])
                low=dlo<=dhi
                # ...at that end of the SHARED FRONTAGE -- not at the blocked circuit's own
                # far corner, which is what ra[2*k+1] gave.  For 33 that put the entry at
                # (7.87,5.40), the opposite end of ET from its manifold, so the feed had to
                # cross the whole floor to reach it and cut through 32's loops seven times.
                c=(lo if low else hi)
                e=[0,0]; e[k]=c; e[1-k]=ra[2*(1-k)] if s>0 else ra[2*(1-k)+1]
                ent[a]=tuple(e)
                cut.setdefault(b,[]).append((a,k,low))
    return ent,cut
HEATENTRY={}; _SHADOW={}

_F0={}
def _first0(nn):
    """Where this circuit's serpentine starts, before any blocker has been cut back. That is
    also where its feed has to arrive, so it is what decides the lane."""
    if nn not in _F0:
        b=_body(HEATFLD0[nn],_cc_of(HEATFLD0[nn],HEAT[nn][2]),
                HEATENTRY.get(nn,HEATPORT[nn]),MARGIN)
        _F0[nn]=b[0] if b else HEATPORT[nn]
    return _F0[nn]

def _fields2(nn):
    """Give up a LANE to each feed that has to cross this circuit -- a 260 mm strip, not half
    the room.  The clip here used to be a half-plane, which threw away everything past the
    feed line; with one blocker whose entry sat at the field edge that was nearly harmless, but
    once the blocker test was fixed circuit 33 acquired three of them and the successive
    half-planes cut it from 22.4 m2 to 4.3.  Subtracting the strip itself costs about 1.3 m2
    per crossing feed, which is what an installer actually loses."""
    f=HEATFLD0[nn]
    for a,k,low in _SHADOW.get(nn,[]):
        c=_first0(a)[k]                              # the feed runs exactly on this line
        ax='x' if k==0 else 'y'; axb='y' if k==0 else 'x'
        g=[]
        for p,ay in f:
            b=_bbox(p); r0,r1=(b[2],b[3]) if k==0 else (b[0],b[1])
            for q in _cut_corridor(p,ax,c-LANEW/2,c+LANEW/2,axb,r0,r1): g.append((q,ay))
        if g: f=g
    return f
HEATFLD={}
_BOD={}
_P1={}
_SPN={}

def _layout(rank):
    """Ports, feed corridors and feed lanes, in that order -- each one needs the one before it.
    Run twice: the first pass has to guess the port order from where the rooms are, the second
    knows where each circuit actually turns off and can order the ports by that instead."""
    global HEATPORT,HEATFLD0,HEATENTRY,_SHADOW,HEATFLD,_F0,_F1
    # _CCS must go too.  It caches the solved pitch per circuit, and the pitch is solved
    # against HEATFLD -- which this function is about to rebuild.  Leaving it would carry
    # pass one's pitches into pass two on fields that no longer exist.
    HEATPORT=_ports(rank); _F0={}; _F1={}; _CCS.clear(); _BOD.clear(); _P1.clear(); _SPN.clear()
    HEATFLD0={nn:_fields(nn) for nn in HEAT}
    HEATENTRY,_SHADOW=_shadow()
    HEATFLD={nn:_fields2(nn) for nn in HEAT}

_CCS={}
def heat_cc(nn):
    """Spine spacing, solved so the built serpentine actually comes out at the circuit's
    drawn `lenkki` length.

    A/m is the right pitch for a serpentine that can use the whole field, but this one
    cannot: _lanes insets by MARGIN all round and the feed corridor takes another bite, so
    at cc = A/m the loops land about 25% short.  Rather than guess a fudge for the lost
    border, bisect on cc and measure -- length falls monotonically as the pitch opens up, so
    ~20 halvings pin it, and the whole set of 19 circuits costs 0.02 s per pass.
    """
    if nn in _CCS: return _CCS[nn]
    fld,m = HEATFLD[nn],HEAT[nn][2]
    ent   = HEATENTRY.get(nn,HEATPORT[nn])
    def blen(cc):
        p=_body(fld,cc,ent,MARGIN)
        return sum(math.dist(p[i],p[i+1]) for i in range(len(p)-1)) if len(p)>1 else 0.0
    lo,hi=0.10,0.60
    if blen(lo)<m: cc=lo                    # cannot reach it even at the tightest pitch
    elif blen(hi)>m: cc=hi
    else:
        for _ in range(20):
            mid=(lo+hi)/2
            if blen(mid)>m: lo=mid
            else:           hi=mid
        cc=(lo+hi)/2
    _CCS[nn]=cc
    return cc

_F1={}
def _first(nn):
    """The serpentine's start on the finished field."""
    if nn not in _F1:
        b=_body(HEATFLD[nn],heat_cc(nn),HEATENTRY.get(nn,HEATPORT[nn]),MARGIN)
        _F1[nn]=b[0] if b else HEATPORT[nn]
    return _F1[nn]

def _ctr(nn):
    b=_bbox(HEAT[nn][3]); return ((b[0]+b[1])/2,(b[2]+b[3])/2)
_layout({nn:_ctr(nn) for nn in HEAT})
_layout({nn:_first(nn) for nn in HEAT})

# how far out of the bar each circuit runs before it may turn: its own trunk lane
STUB0=0.22

def _jdist(nn):
    """Gap between a circuit's port and the part of its room the serpentine may use."""
    b=_bbox([p for f,_ in HEATFLD[nn] for p in f]); p=HEATPORT[nn]
    return math.hypot(max(b[0]-p[0],p[0]-b[1],0.0),max(b[2]-p[1],p[1]-b[3],0.0))

def _side(nn):
    """Which side of the jakotukki bar this circuit's field lies on, how far it has to travel
    ALONG the bar before it turns off, and which index that bar axis is. The measure is the
    real turn-off point, not the middle of the room: what a lane has to clear is the pipes that
    are still beside it when it leaves."""
    k=0 if HEATJT[nn[0]][3]=='x' else 1  # ports vary along k; pipes leave along 1-k
    c=_first(nn); jp=HEATPORT[nn]
    return (1 if c[1-k]>=jp[1-k] else -1, abs(c[k]-jp[k]), k)

def _stub(nn,jp,poly):
    """The trunk leg out of the jakotukki. Pipes leave the bar perpendicular to it and then run
    to their rooms in parallel lanes, so no two leads share a corridor line.
    Which lane a circuit gets is not arbitrary. Leaving its lane means crossing every lane
    between it and the rooms, so the circuit that turns off SOONEST must already be on the lane
    nearest the rooms, and the one running furthest along the bar stays hard against it. Sort by
    run length descending and hand out lanes from the bar outwards and nothing crosses anything.
    A circuit whose room starts at the manifold needs no trunk: forcing one on it only drives
    the lead back into its own serpentine."""
    if _jdist(nn)<0.5: return None
    s,_,k=_side(nn)
    cs=[c for c in sorted(HEAT) if c[0]==nn[0] and _jdist(c)>=0.5 and _side(c)[0]==s]
    cs.sort(key=lambda c:(-_side(c)[1],c))
    r=STUB0+cs.index(nn)*PORTPITCH
    return (jp[0],jp[1]+s*r) if k==0 else (jp[0]+s*r,jp[1])

def _bodyof(nn):
    """A circuit's serpentine, without its lead.  The body depends only on the field, the
    pitch and the entry point -- never on anyone's lead -- so every circuit's loops can be
    known before a single feed is routed, and a feed can be scored against the real pipe."""
    if nn not in _BOD:
        _BOD[nn]=_body(HEATFLD[nn],heat_cc(nn),HEATENTRY.get(nn,HEATPORT[nn]),MARGIN)
    return _BOD[nn]

def _hitbodies(route,bods):
    """How many times a candidate lead actually crosses another circuit's pipe.

    _onothers already asks whether a lead runs over another circuit's FLOOR, but floor is not
    pipe: the corridor and the shadow lanes are cut out of that floor precisely so a feed can
    pass through it, and a lead that uses them scores a penalty it does not deserve while a
    lead that slips between two loops and out the far side scores none at all.  Count the
    intersections themselves -- that is the thing being minimised."""
    n=0
    for i in range(len(route)-1):
        a,b=route[i],route[i+1]
        x0,x1=(a[0],b[0]) if a[0]<=b[0] else (b[0],a[0])
        y0,y1=(a[1],b[1]) if a[1]<=b[1] else (b[1],a[1])
        for q,bb in bods:
            if bb[1]<x0-1e-9 or bb[0]>x1+1e-9 or bb[3]<y0-1e-9 or bb[2]>y1+1e-9: continue
            for jj in range(len(q)-1):
                if _seg_hit(a,b,q[jj],q[jj+1]): n+=1
    return n

def _mkspine(nn,obst):
    """The single continuous 20 mm pipe of one circuit, manifold out and back.
    The sheet draws one serpentine line per circuit, but that line is the supply/return
    PAIR, so the drawn line is the spine at c/c = 2*area/lenkki and the real pipe is its
    two parallel offsets, joined by a U at the far end. That is a proper counterflow
    layout: every run has its partner beside it and nothing ever crosses itself."""
    kerros,rooms,m,poly,z=HEAT[nn]
    fields=HEATFLD[nn]
    jp=HEATPORT[nn]; stub=_stub(nn,jp,[p for f,_ in fields for p in f])
    cc=heat_cc(nn)
    # the ring pair must clear the first lane pair by a pipe diameter, and no segment may be
    # shorter than the offset needs to turn a corner: both come back to the ring-to-lane step.
    # mn = 3.0*d must stay strictly UNDER the ring-to-lane step GAP, or that segment is
    # dropped on a floating-point tie and the route collapses into a diagonal.
    # d is the half-separation of the flow/return pair AND, through mn=3*d in _mklen, the
    # shortest segment the spine may keep.  At cc/4 that threshold is 0.75*cc, but a U-turn's
    # run along the mid-line is only cc/2 -- so EVERY turn was shorter than the limit, got its
    # point dropped, and left a diagonal where an orthogonal turn should be.  cc/6.5 puts the
    # threshold at 0.46*cc, just under the turn, so the turns survive.
    d=min(cc/6.5,(GAP-0.024)/2,GAP/3.4)
    body=_body(fields,cc,HEATENTRY.get(nn,jp),MARGIN)
    if len(body)<2: return [],d
    polys=[f[0] for f in fields]
    others=[g for m in HEAT if m!=nn and m[0]==nn[0] for g,_ in HEATFLD[m]]
    best=None
    for lead,skip in _leads(jp,body[0],fields[0][0],stub):
        sp=_mklen(lead[:-1]+body,3.0*d)
        # score the FINISHED pair, not the spine: a dropped point leaves a shallow diagonal
        # that the spine never notices because only its two offsets clash. The diagonal and
        # length terms are tie-breakers well below one clash, so correctness still wins.
        s=(10*_selfhits(_offset(sp,-d)+list(reversed(_offset(sp,d))))
           +_outside(sp,polys,skip)+6.0*_onothers(lead,others)+20.0*_hitbodies(lead,obst)
           +20.0*_diag(sp)+0.10*_plen(lead))
        if best is None or s<best[0]: best=(s,sp)
    return best[1],d

def _obst(nn,src):
    out=[]
    for m in HEAT:
        if m==nn or m[0]!=nn[0]: continue
        q=src(m)
        if len(q)>1: out.append((q,_bbox(q)))
    return out

_P1={}
def _spine1(nn):
    """First pass: route each feed clear of the other circuits' LOOPS.  Loops are known before
    any feed is routed -- they depend only on field, pitch and entry -- so this pass needs
    nothing from anybody else and cannot chase its own tail."""
    if nn not in _P1: _P1[nn]=_mkspine(nn,_obst(nn,_bodyof))
    return _P1[nn]

_SPN={}
def heat_spine(nn):
    """Second pass: now that every feed has a provisional route, keep clear of those too.

    Two feeds can each be clear of every loop on the floor and still cross each OTHER in the
    corridor, which is what 51, 52 and 53 did -- three crossings between three leads, none of
    them touching a single loop.  A feed's route depends on the other feeds, so this cannot be
    solved in one pass; it can be solved in two, because pass one depends only on the loops.
    Fixed at two passes deliberately: a third would start chasing decisions the second pass
    made, and the result would depend on the order the circuits happen to be visited in."""
    if nn not in _SPN: _SPN[nn]=_mkspine(nn,_obst(nn,lambda m:_spine1(m)[0]))
    return _SPN[nn]

def heat_path(nn):
    """The finished pipe: the spine's two parallel offsets, joined by a U at the far end."""
    sp,d=heat_spine(nn)
    if len(sp)<2: return []
    sp=_fillet(sp,1.3*d)
    return _offset(sp,-d)+list(reversed(_offset(sp,d)))

def build_heat(B):
    # zone patch above the Room_ finish (top 0.020) and rugs (top 0.038); the pipe rides on it
    for nn,(kerros,rooms,m,poly,z) in HEAT.items():
        B.floor=kerros
        B.slab(f'Heat_{kerros}_{nn}',poly,z+0.042,z+0.046,'HeatOff')
        path=heat_path(nn)
        if path: B.tube(f'Heat_{kerros}_{nn}.pipe',path,0.011,z+0.057,'HeatPipe')
    for j,(kerros,jx,jy,o) in HEATJT.items():
        B.floor=kerros
        zj={'kellari':Z_K,'1krs':0.0,'2krs':Z_2}[kerros]
        if o=='x': B.box(f'Heat_{kerros}_JT{j}',(jx-0.22,jx+0.22),(jy-0.035,jy+0.035),(zj+0.25,zj+0.75),'Metal')
        else:      B.box(f'Heat_{kerros}_JT{j}',(jx-0.035,jx+0.035),(jy-0.22,jy+0.22),(zj+0.25,zj+0.75),'Metal')

def build_all(B):
    build_kellari(B); build_krs1(B); build_krs2(B); build_roof(B); build_katos(B); build_lights(B); build_heat(B)
