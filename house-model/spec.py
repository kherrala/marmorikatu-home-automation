# Marmorikatu 10 — geometry spec v2, rebuilt from DWG-derived vector extraction
# (1:50 architect PDFs) + elevations. Units m. Origin: outer SW corner of 1krs.
# x: 0=pohjoinen paaty -> 16.98=etela paaty (real compass N->S)
# y: 0=lansi julkisivu (entrance side) -> 7.98=ita julkisivu
# z: 0 = 1krs floor top (+135.90). Kellari +132.86, 2krs +138.91.
EXT = 0.30   # exterior wall (drawn 0.36 with cladding; 0.30 keeps openings crisp)
KXT = 0.34   # kellari concrete+render
INT = 0.10
Z_K = -3.04; Z_2 = 3.01
H_K = 2.54   # kellari clear (2540)
H_1 = 2.56   # 1krs clear (2560); 2krs slab 2.56->3.01
H_1E= 3.01   # 1krs exterior walls run up over the slab edge (continuous cladding)
H_L = 2.60   # living-wing walls (flat ceiling per owner)
H_2 = 2.58   # 2krs interior (2580)
H_2E= 3.19   # 2krs long-side ext walls up to roof underside

P1 = [(0,0),(14.28,0),(14.28,3.30),(16.98,3.30),(16.98,7.98),(0,7.98)]
PK = [(0.07,0.07),(13.83,0.07),(13.83,3.37),(16.91,3.37),(16.91,7.91),(0.07,7.91)]
P2 = [(0,0),(10.98,0),(10.98,7.98),(0,7.98)]

def W(kind,a0,a1,zb=None,zt=None):
    if kind in ('door','glassdoor'): zb,zt = 0.0,(zt or 2.10)
    if kind=='win': zb = 0.9 if zb is None else zb; zt = 2.0 if zt is None else zt
    return (kind,a0,a1,zb,zt)

def wall_x(B,name,x,y0,y1,z0,h,t,ops=(),mat='WallInt'): _wall(B,name,'x',x,y0,y1,z0,h,t,ops,mat)
def wall_y(B,name,y,x0,x1,z0,h,t,ops=(),mat='WallInt'): _wall(B,name,'y',y,x0,x1,z0,h,t,ops,mat)
def _wall(B,name,axis,c,a0,a1,z0,h,t,ops,mat):
    ops = sorted(ops,key=lambda o:o[1]); cur=a0; i=0
    def emit(nm,lo,hi,zb,zt):
        if hi-lo<=0.005 or zt-zb<=0.005: return
        if axis=='x': B.box(nm,(c-t/2,c+t/2),(lo,hi),(z0+zb,z0+zt),mat)
        else:         B.box(nm,(lo,hi),(c-t/2,c+t/2),(z0+zb,z0+zt),mat)
    for (kind,o0,o1,zb,zt) in ops:
        emit(f'{name}.seg{i}',cur,o0,0,h); i+=1
        emit(f'{name}.sill{i}',o0,o1,0,zb)
        emit(f'{name}.lint{i}',o0,o1,zt,h)
        g=0.03
        if kind in ('win','glassdoor'):
            if axis=='x': B.box(f'{name}.glass{i}',(c-g/2,c+g/2),(o0+0.02,o1-0.02),(z0+zb+0.02,z0+zt-0.02),'Glass')
            else:         B.box(f'{name}.glass{i}',(o0+0.02,o1-0.02),(c-g/2,c+g/2),(z0+zb+0.02,z0+zt-0.02),'Glass')
        if kind in ('door','glassdoor') and kind=='door':
            d=0.04
            if axis=='x': B.box(f'{name}.leaf{i}',(c-d/2,c+d/2),(o0+0.01,o1-0.01),(z0,z0+zt-0.02),'Door')
            else:         B.box(f'{name}.leaf{i}',(o0+0.01,o1-0.01),(c-d/2,c+d/2),(z0,z0+zt-0.02),'Door')
        cur=o1
    emit(f'{name}.seg{i}',cur,a1,0,h)

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
def rug(B,nm,x0,x1,y0,y1,mat='Rug'): B.box(nm,(x0,x1),(y0,y1),(0.005,0.02),mat)
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
    wall_y(B,'K.wS',0.07+t/2,0.07,13.83,Z_K,h,t,mat='ConcreteW')
    wall_x(B,'K.wNW',13.83+t/2,0.07,3.37,Z_K,h,t,          # notch west wall, exit door
           ops=[W('door',2.18,3.19,0,2.05)],mat='ConcreteW')
    wall_y(B,'K.wNN',3.37+t/2,13.83,16.91,Z_K,h,t,mat='ConcreteW')
    wall_x(B,'K.wE',16.91-t/2,3.37,7.91,Z_K,h,t,
           ops=[W('win',5.08,6.18,1.75,2.30)],mat='ConcreteW')
    wall_y(B,'K.wN',7.91-t/2,0.07,16.91,Z_K,h,t,
           ops=[W('win',11.84,12.94,1.75,2.30),W('win',14.54,15.64,1.75,2.30)],mat='ConcreteW')
    wall_x(B,'K.wW',0.07+t/2,0.07,7.91,Z_K,h,t,mat='ConcreteW')
    wall_x(B,'K.div',10.805,0.41,7.57,Z_K,h,0.27,
           ops=[W('door',2.28,3.19,0,2.05)],mat='ConcreteW')
    # WC in the NE corner (built after the drawings; under the 1krs PH/KHH plumbing)
    wall_x(B,'K.wc.e',2.00,5.90,7.57,Z_K,H_K,INT)
    wall_y(B,'K.wc.s',5.90,0.41,2.05,Z_K,H_K,INT,ops=[W('door',0.95,1.80)])
    B.room('Room_kellari_WC',[(0.41,5.95),(1.95,5.95),(1.95,7.57),(0.41,7.57)],'Tile',z=Z_K)
    B.room('Room_kellari_VAR1',[(0.41,0.41),(10.67,0.41),(10.67,7.57),(2.10,7.57),(2.10,5.85),(0.41,5.85)],'ConcreteF',z=Z_K)
    B.room('Room_kellari_VAR2',[(10.94,0.41),(13.66,0.41),(13.66,3.54),(16.57,3.54),(16.57,7.57),(10.94,7.57)],'ConcreteF',z=Z_K)
    B.box('K.shelfV2',(15.2,16.5),(6.9,7.5),(Z_K,Z_K+2.0),'WoodFurn')
    B.box('K.bench',(11.2,13.2),(0.5,1.1),(Z_K,Z_K+0.9),'WoodFurn')
    # --- big room as rec room (owner): billiard N end, screen+sofa S end, desk SW
    B.zoff=Z_K
    toilet(B,'K.wc.wc',0.85,7.18,'S')
    B.box('K.wc.basin',(1.45,1.85),(7.25,7.55),(0.55,0.87),'Ceramic')
    B.box('K.pool.body',(1.03,3.53),(1.63,3.03),(0.55,0.75),'WoodFurn')       # billiard
    B.box('K.pool.felt',(1.15,3.41),(1.75,2.91),(0.75,0.78),'SofaGreen')
    B.box('K.pool.railW',(1.03,1.15),(1.63,3.03),(0.75,0.83),'WoodFurn')
    B.box('K.pool.railE',(3.41,3.53),(1.63,3.03),(0.75,0.83),'WoodFurn')
    B.box('K.pool.railS',(1.15,3.41),(1.63,1.75),(0.75,0.83),'WoodFurn')
    B.box('K.pool.railN',(1.15,3.41),(2.91,3.03),(0.75,0.83),'WoodFurn')
    for i,(lx,ly) in enumerate([(1.08,1.68),(3.32,1.68),(1.08,2.87),(3.32,2.87)]):
        B.box(f'K.pool.leg{i}',(lx,lx+0.16),(ly,ly+0.16),(0,0.55),'WoodFurn')
    B.box('K.cuerack',(1.60,2.40),(0.46,0.50),(1.05,1.95),'WoodFurn')          # cues on the W wall
    B.box('K.screen.frame',(6.88,9.88),(7.51,7.55),(0.32,2.36),'TVBlack')      # 3x2 m screen, S end
    B.box('K.screen.face',(6.98,9.78),(7.495,7.51),(0.42,2.26),'White')
    sofa(B,'K.sofaA',6.78,9.98,3.93,4.88,'S','FabricBlue')                     # corner sofa facing screen
    sofa(B,'K.sofaB',6.88,7.78,4.88,6.88,'W','FabricBlue')
    rug(B,'K.rug',7.9,10.1,5.0,7.2)
    B.box('K.media',(10.15,10.65),(7.10,7.46),(0,0.40),'Cabinet')              # AV cabinet beside screen
    table(B,'K.desk',9.10,10.50,0.50,1.25,0.74)                                # office desk, SW corner
    B.box('K.monitor',(9.45,10.15),(0.56,0.60),(0.86,1.28),'TVBlack')
    chair(B,'K.deskch',9.80,1.75,0)
    B.zoff=0.0

# ================================================================= 1. KRS
def build_krs1(B):
    B.floor='1krs'
    B.slab('F1.slab',P1,-0.50,0,'Concrete')
    e=EXT/2
    # exterior walls — openings from vector extraction
    wall_y(B,'F1.wS.blk',0+e,0,10.98,0,H_1E,EXT,mat='WallExt',ops=[
        W('win',1.64,2.74,1.15,2.05),                       # MH
        W('door',4.88,5.78),W('glassdoor',5.78,6.20),        # front door + sidelight
        W('win',8.34,9.44,1.15,2.05)])                       # kitchen
    wall_y(B,'F1.wS.liv',0+e,10.98,14.28,0,H_L,EXT,mat='WallExt',
        ops=[W('win',11.29,13.89,0.45,2.10)])                # dining glazing
    wall_x(B,'F1.wE.din',14.28-e,0,3.30,0,H_L,EXT,mat='WallExt',ops=[
        W('win',0.80,1.60,0.45,2.05),W('win',1.70,2.50,0.45,2.05),
        W('glassdoor',2.60,3.28,0,2.05)])                    # terrace door at notch corner
    wall_y(B,'F1.wS.notch',3.30+e,14.28,16.98,0,H_L,EXT,mat='WallExt',
        ops=[W('win',14.55,16.65,0.45,2.10)])                # glazing continues over the terrace (LANSI)
    wall_x(B,'F1.wE',16.98-e,3.30,7.98,0,H_L,EXT,mat='WallExt',ops=[
        W('win',4.10,5.20,0.35,2.05),W('win',5.30,6.40,0.35,2.05),W('win',6.50,7.60,0.35,2.05)])
    wall_y(B,'F1.wN.liv',7.98-e,10.98,16.98,0,H_L,EXT,mat='WallExt',
        ops=[W('win',11.84,12.94,1.94,2.45),W('win',14.54,15.64,1.94,2.45)])   # ITA: high band, same size as kellari wins
    wall_y(B,'F1.wN.blk',7.98-e,0,10.98,0,H_1E,EXT,mat='WallExt',ops=[
        W('win',2.84,3.94,1.94,2.45),                        # PH: high short strip (ITA elev)
        W('win',7.04,7.54,1.03,2.45)])                       # KHH tall narrow strip (ITA elev)
    # LP vertical-slat column at the KHH/KPH window stack (ITA elevation)
    B.box('F1.slat.c.lo',(7.04,7.54),(7.99,8.05),(-0.45,1.03),'Slat')
    B.box('F1.slat.c.mid',(7.04,7.54),(7.99,8.05),(2.45,3.01),'Slat')
    # roof-access ladder on the east facade at x~9.0-9.4 (drawn in ITA elevation)
    for lx in (9.01,9.41):
        B.box(f'F1.ladder.r{lx:.2f}',(lx-0.02,lx+0.02),(8.02,8.07),(-0.40,6.15),'Metal')
    nrung=int((6.0-0.0)/0.30)
    for i in range(nrung+1):
        B.box(f'F1.ladder.g{i}',(9.01,9.41),(8.055,8.085),(0.0+i*0.30-0.015,0.0+i*0.30+0.015),'Metal')
    wall_x(B,'F1.wW',0+e,0,7.98,0,H_1E,EXT,mat='WallExt',ops=[
        W('door',4.20,5.15,0,2.05),                          # TEKN exterior door (POHJOINEN)
        W('win',6.40,6.92,1.35,1.90)])                       # sauna window
    # interior walls
    wall_y(B,'F1.nb.w',5.45,0.30,4.41,0,H_1,INT,ops=[W('door',3.55,4.30)])   # PH door to hall
    wall_y(B,'F1.nb.e',5.60,4.41,9.64,0,H_1,INT,
        ops=[W('door',6.85,7.60),W('door',8.65,9.40)])       # KHH + VH doors
    wall_x(B,'F1.lh_ph',2.44,5.45,7.68,0,H_1,INT,ops=[W('door',5.75,6.45)])
    wall_x(B,'F1.ph_khh',4.44,5.45,7.68,0,H_1,INT,ops=[W('door',6.55,7.30)])
    wall_x(B,'F1.khh_vh',7.92,5.60,7.68,0,H_1,INT)
    wall_x(B,'F1.vh_st',9.64,5.55,7.68,0,H_1,INT)
    wall_x(B,'F1.st_liv',10.84,5.52,7.68,0,2.56,INT)
    wall_x(B,'F1.tekn_wc',2.49,3.90,5.45,0,H_1,INT)
    wall_x(B,'F1.wc_et',4.10,3.90,5.45,0,H_1,INT,ops=[W('door',4.35,5.10)])
    wall_y(B,'F1.mh_n',3.90,0.30,4.10,0,H_1,INT)
    wall_x(B,'F1.mh_e',3.75,0.30,3.90,0,H_1,INT,ops=[W('door',2.55,3.40)])
    wall_y(B,'F1.tk_n',2.47,3.75,6.34,0,H_1,INT,ops=[W('door',5.00,5.90)])
    wall_y(B,'F1.vh2_n',2.52,6.34,7.92,0,H_1,INT,ops=[W('door',6.60,7.35)])
    wall_x(B,'F1.tk_vh2',6.34,0.30,2.47,0,H_1,INT)
    wall_x(B,'F1.tk_w',4.75,0.30,2.47,0,H_1,INT)
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
    R('Room_1krs_TK',[(4.80,0.30),(6.29,0.30),(6.29,2.42),(4.80,2.42)],'Tile')
    R('Room_1krs_VH2',[(6.39,0.30),(7.87,0.30),(7.87,2.47),(6.39,2.47)],'Wood')
    R('Room_1krs_ET',[(3.80,0.30),(4.75,0.30),(4.75,2.42),(3.80,2.42)],'Wood')  # west strip
    R('Room_1krs_ET2',[(3.80,2.52),(7.87,2.52),(7.87,5.40),(3.80,5.40)],'Wood')
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
    B.cyl('F1.tekn.tank',0.85,4.65,0,1.75,0.31,'Appliance')
    B.box('F1.tekn.panel',(1.55,2.10),(5.32,5.39),(1.0,1.6),'Metal')
    rug(B,'F1.mh.rug',0.8,3.4,0.7,3.3)
    sofa(B,'F1.mh.sofa',0.45,1.40,0.70,2.70,'W','SofaGreen')
    B.cyl('F1.mh.side',1.80,1.05,0,0.50,0.25,'WoodFurn')
    wardrobe(B,'F1.mh.ward',2.73,3.12,0.90,2.60,2.10)                   # per plan, e-wall closet
    plant(B,'F1.mh.plant',3.30,3.40,0.8)
    B.box('F1.tk.bench',(4.85,5.75),(0.40,0.75),(0.15,0.45),'WoodFurn')
    B.box('F1.tk.rack',(4.83,5.77),(0.34,0.39),(1.65,1.95),'WoodFurn')
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
    for i,(y0,y1) in enumerate([(9.56,10.16),(10.18,10.76)]):
        B.box(f'F1.kit.tall{i}',(y0+0.01,y1-0.01),(0.77,1.37),(0,2.20),'Appliance')
    B.box('F1.kit.rad',(8.39,9.39),(0.53,0.68),(0.12,0.55),'White')          # radiator under window
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
    # vertical slat cladding bands at the window columns — split around the openings (LANSI)
    for nm,(x0,x1) in {'a':(1.71,2.79),'b':(8.44,9.52)}.items():
        B.box(f'F1.slat.{nm}lo',(x0,x1),(-0.08,-0.02),(0.08,1.13),'Slat')   # below 1krs window
        B.box(f'F1.slat.{nm}hi',(x0,x1),(-0.08,-0.02),(2.07,3.42),'Slat')   # between 1krs and 2krs windows
    B.roofquad('F1.canopy',[(4.20,0.05,2.55),(6.90,0.05,2.55),(6.90,-1.30,2.25),(4.20,-1.30,2.25)],0.08,'Roof')
    B.box('F1.cpost1',(4.32,4.44),(-1.28,-1.16),(-0.03,2.20),'White')
    B.box('F1.cpost2',(6.66,6.78),(-1.28,-1.16),(-0.03,2.20),'White')
    B.floor='terassi'
    # terrace (west+south of wing), slat skirt, steps
    B.slab('T.deck',[(6.00,-3.40),(16.98,-3.40),(16.98,3.30),(14.28,3.30),(14.28,0.0),(6.00,0.0)],-0.12,-0.03,'Deck')
    def railseg(nm,x0,x1,y0,y1,axis='x'):
        B.box(nm+'.top',(x0,x1),(y0,y1),(0.86,0.94),'White')            # white top rail
        for j,(z0,z1) in enumerate([(0.14,0.26),(0.32,0.44),(0.50,0.62),(0.66,0.78)]):
            B.box(f'{nm}.sl{j}',(x0,x1),(y0,y1),(z0,z1),'SlatGray')     # gray louver slats
        n=max(1,int((x1-x0 if axis=='x' else y1-y0)/1.8))
        for k in range(n+1):
            if axis=='x': px=x0+k*(x1-x0)/n; B.box(f'{nm}.p{k}',(px-0.05,px+0.05),(y0-0.01,y1+0.01),(0.0,0.94),'White')
            else:         py=y0+k*(y1-y0)/n; B.box(f'{nm}.p{k}',(x0-0.01,x1+0.01),(py-0.05,py+0.05),(0.0,0.94),'White')
    railseg('T.rail.s1',6.0,16.98,-3.40,-3.32)                 # continuous south rail (no opening)
    railseg('T.rail.w',6.0,6.08,-3.40,-1.60,axis='y')   # opening y -1.6..0: terrace entry beside the front door
    railseg('T.rail.e',16.90,16.98,-3.40,3.30,axis='y')
    def louver(nm,xs,ys,z0,z1,board=0.14,gap=0.11,mat='SlatGray'):
        z=z0; i=0
        while z<z1-0.01:
            B.box(f'{nm}.b{i}',xs,ys,(z,min(z1,z+board)),mat); z+=board+gap; i+=1
    # under-terrace enclosure: real interleaved louvers; access opening faces the kellari door
    louver('T.skirt.s',(6.0,16.92),(-3.40,-3.34),-2.96,-0.12)
    louver('T.skirt.w',(6.0,6.06),(-3.40,0.0),-2.96,-0.12)
    louver('T.skirt.e',(16.92,16.98),(-3.40,1.95),-2.96,-0.12)   # gap y1.95..3.30 = door front
    # basement-level concrete yard slab: under the whole terrace + outside the kellari entrance
    B.slab('T.ground',[(5.80,-4.70),(17.30,-4.70),(17.30,3.40),(13.60,3.40),(13.60,0.0),(5.80,0.0)],-3.12,-3.00,'ConcreteF')
    for i,(px,py) in enumerate([(14.55,-3.30),(15.90,-3.30)]):
        B.box(f'T.dpost{i}',(px-0.06,px+0.06),(py-0.06,py+0.06),(-3.00,-0.12),'Deck')
    # pergola: white frame, clear canopy running the full length of the terrace
    for i,px in enumerate([8.85,11.55,14.15,16.80]):
        B.box(f'T.perg.post{i}',(px-0.06,px+0.06),(-3.28,-3.16),(-0.03,2.02),'White')
    B.box('T.perg.beam1',(8.70,16.98),(-3.30,-3.16),(2.02,2.14),'White')
    B.box('T.perg.beam2',(8.70,16.98),(-0.20,-0.06),(2.36,2.48),'White')
    B.roofquad('T.perg.canopy',[(8.70,0.0,2.51),(16.98,0.0,2.51),(16.98,-3.45,2.18),(8.70,-3.45,2.18)],0.03,'Canopy')
    B.box('T.perg.lattice',(8.72,8.78),(-3.16,-1.75),(0.94,2.05),'Canopy')   # translucent trellis
    # terraced garden beds on the slope behind the carport: from the drive ledge
    # (+135.30 / z-0.60) all the way down to the basement yard (+132.90 / z-3.00)
    B.slab('T.ledge.slab',[(9.00,-8.90),(10.30,-8.90),(10.30,-5.30),(9.00,-5.30)],-0.72,-0.60,'Plant')
    B.slab('T.back.slab',[(10.30,-8.90),(17.30,-8.90),(17.30,-4.70),(10.30,-4.70)],-3.12,-3.00,'ConcreteF')
    TIER=[-0.60,-1.05,-1.70,-2.35,-3.00]
    for i in range(4):
        x0=10.30+i*1.15
        B.box(f'T.rwall{i}',(x0-0.12,x0),(-8.90,-5.30),(TIER[i+1]-0.45,TIER[i]),'Block')
        B.box(f'T.rbed{i}',(x0,x0+1.03),(-8.85,-5.35),(TIER[i+1]-0.30,TIER[i+1]-0.12),'Soil')
        B.box(f'T.rveg{i}',(x0+0.06,x0+0.97),(-8.75,-5.45),(TIER[i+1]-0.14,TIER[i+1]),'Plant')
    # outdoor stair to the basement yard: from the drive (-0.55) to the slab (-3.00)
    for i in range(14):
        xs=9.30+i*0.33; zt=-0.55-(i+1)*0.175
        B.box(f'T.gstep{i}',(xs,xs+0.35),(-5.25,-4.15),(zt-0.18,zt),'ConcreteF')
    # rattan lounge set under the canopy (per photo) + dining set east
    sofa(B,'T.sofa1',9.25,11.75,-3.10,-2.40,'S','Rattan')
    sofa(B,'T.sofa2',11.05,11.75,-2.40,-1.15,'E','Rattan')
    B.box('T.ctable',(9.65,10.85),(-2.25,-1.45),(0.12,0.48),'Rattan')
    B.cyl('T.reel',9.10,-1.05,0,0.52,0.35,'WoodFurn')
    B.cyl('T.tbl',12.70,-1.70,0,0.72,0.60,'Rattan')
    for i,(cx,cy) in enumerate([(12.0,-1.0),(13.4,-1.0),(12.0,-2.4),(13.4,-2.4)]):
        chair(B,f'T.ch{i}',cx,cy,0 if cy>-1.7 else 180,'Rattan')
    for i,y0 in enumerate([0.70,1.80]):
        B.box(f'T.lounge{i}.seat',(15.35,16.15),(y0,y0+0.75),(0.15,0.32),'Rattan')
        B.box(f'T.lounge{i}.back',(15.35,15.60),(y0,y0+0.75),(0.32,0.75),'Rattan')
    plant(B,'T.pl1',14.60,0.45,1.0); plant(B,'T.pl2',16.55,-3.05,0.9)
    B.slab('T.porch',[(4.26,-1.45),(6.82,-1.45),(6.82,0.0),(4.26,0.0)],-0.12,-0.03,'Deck')
    # paved walkway along the facade at yard level (+135.35 / z-0.55)
    B.slab('T.walk',[(0.80,-1.45),(4.26,-1.45),(4.26,0.0),(0.80,0.0)],-0.61,-0.55,'Paver')
    for i,(tx,ty) in enumerate([(1.6,-1.1),(3.3,-1.1)]):
        B.cyl(f'T.thuja{i}.pot',tx,ty,-0.55,-0.23,0.15,'Pot')
        B.cyl(f'T.thuja{i}.tr',tx,ty,-0.17,0.15,0.05,'WoodFurn')
        B.sph(f'T.thuja{i}.fa',tx,ty,0.50,0.30,'Plant'); B.sph(f'T.thuja{i}.fb',tx,ty,0.95,0.22,'Plant')
    # white picnic set near the walkway end of the terrace (photo)
    table(B,'T.picnic',6.60,7.80,-2.65,-1.85,0.74,'White')
    B.box('T.picbn1',(6.55,7.85),(-3.05,-2.75),(0.30,0.46),'White')
    B.box('T.picbn2',(6.55,7.85),(-1.75,-1.45),(0.30,0.46),'White')
    for i in range(2):
        B.box(f'T.porchstep{i}',(4.26,6.82),(-1.45-(i+1)*0.30,-1.45-i*0.30),(-0.12-(i+1)*0.17,-0.03-(i+1)*0.17),'Deck')
    B.slab('T.stoopW',[(-1.35,4.05),(0,4.05),(0,5.35),(-1.35,5.35)],-0.12,-0.03,'Deck')
    for i in range(3):
        B.box(f'T.stoopWstep{i}',(-1.65-i*0.30,-1.35-i*0.30),(4.05,5.35),(-0.29-i*0.17,-0.20-i*0.17),'Deck')

# ================================================================= 2. KRS
def build_krs2(B):
    B.floor='2krs'
    B.slab('F2.slab.a',[(0.10,0.10),(8.81,0.10),(8.81,7.88),(0.10,7.88)],2.56,Z_2,'Concrete')
    B.slab('F2.slab.b',[(8.81,0.10),(10.88,0.10),(10.88,5.50),(8.81,5.50)],2.56,Z_2,'Concrete')
    B.slab('F2.slab.c',[(10.68,5.50),(10.88,5.50),(10.88,7.88),(10.68,7.88)],2.56,Z_2,'Concrete')
    e=EXT/2; z=Z_2
    wall_y(B,'F2.wS',0+e,0,10.98,z,H_2E,EXT,mat='WallExt2',ops=[
        W('win',1.67,2.77,0.45,2.05),W('win',4.42,5.52,0.45,2.05),
        W('win',5.62,6.72,0.45,2.05),W('win',8.37,9.47,0.45,2.05)])
    wall_x(B,'F2.wE',10.98-e,0,7.98,z,H_2,EXT,mat='WallExt2',
        ops=[W('win',1.34,2.44,0.80,2.00)])
    wall_y(B,'F2.wN',7.98-e,0,10.98,z,H_2E,EXT,mat='WallExt2',
        ops=[W('win',7.07,7.57,1.03,2.45)])                  # KPH tall narrow strip (ITA elev)
    B.box('F2.slat.c',(7.04,7.54),(7.99,8.05),(3.01,4.04),'Slat')  # LP slat between the stacked windows
    wall_x(B,'F2.wW',0+e,0,7.98,z,H_2,EXT,mat='WallExt2',ops=[
        W('win',1.64,2.74,0.80,2.00),                                        # MH2 (plan vector)
        W('win',4.83,5.33,0.30,2.05),W('door',5.43,6.34,0,2.05),W('win',6.44,6.94,0.30,2.05),
        W('win',6.98,7.50,0.25,1.80)])                                       # MH stacked gable strip (photo)
    # interior
    wall_y(B,'F2.mh_s',4.60,0.30,5.35,z,H_2,INT,ops=[W('door',4.35,5.25)])
    wall_y(B,'F2.band',5.60,5.35,8.71,z,H_2,INT,ops=[W('door',5.60,6.30),W('door',6.80,7.55)])
    wall_x(B,'F2.mh_vh',5.35,4.60,7.68,z,H_2,INT)
    wall_x(B,'F2.vh_kph',6.60,5.60,7.68,z,H_2,INT)
    wall_x(B,'F2.kph_st',8.71,5.60,7.68,z,H_2,INT)
    wall_x(B,'F2.sw_e',3.65,0.30,3.98,z,H_2,INT)
    wall_y(B,'F2.sw_n',3.98,0.30,3.65,z,H_2,INT,ops=[W('door',2.65,3.50)])
    wall_x(B,'F2.se_w',7.39,0.30,3.98,z,H_2,INT)
    wall_y(B,'F2.se_n',3.98,7.39,10.68,z,H_2,INT,ops=[W('door',7.60,8.45)])
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
    R('Room_2krs_MH',[(0.30,4.65),(5.30,4.65),(5.30,7.68),(0.30,7.68)],'Wood',z=Z_2)
    R('Room_2krs_VH',[(5.40,5.65),(6.55,5.65),(6.55,7.68),(5.40,7.68)],'Wood',z=Z_2)
    R('Room_2krs_KPH',[(6.65,5.65),(8.66,5.65),(8.66,7.68),(6.65,7.68)],'Tile',z=Z_2)
    R('Room_2krs_AULA',[(3.70,0.30),(7.34,0.30),(7.34,3.93),(10.68,3.93),(10.68,5.45),(0.30,5.45),(0.30,4.03),(3.70,4.03)],'Wood',z=Z_2)
    R('Room_2krs_MH2',[(0.30,0.30),(3.60,0.30),(3.60,3.93),(0.30,3.93)],'Wood',z=Z_2)
    R('Room_2krs_MH3',[(7.44,0.30),(10.68,0.30),(10.68,3.93),(7.44,3.93)],'Wood',z=Z_2)
    # balcony: dark cantilevered box + white louver railing (photo)
    B.slab('F2.balc',[(-1.21,3.99),(0,3.99),(0,7.78),(-1.21,7.78)],Z_2-0.30,Z_2-0.02,'DarkWood')
    B.slab('F2.balcT',[(-1.19,4.01),(0,4.01),(0,7.76),(-1.19,7.76)],Z_2-0.02,Z_2+0.02,'Deck')
    for nm,(x0,x1,y0,y1) in {'w':(-1.21,-1.13,3.99,7.78),'s':(-1.21,0,3.99,4.07),'n':(-1.21,0,7.70,7.78)}.items():
        B.box(f'F2.brail.{nm}.top',(x0,x1),(y0,y1),(Z_2+0.96,Z_2+1.06),'White')
        for j,(z0,z1) in enumerate([(0.10,0.24),(0.32,0.46),(0.54,0.68),(0.76,0.90)]):
            B.box(f'F2.brail.{nm}.sl{j}',(x0,x1),(y0,y1),(Z_2+z0,Z_2+z1),'White')
    B.zoff=Z_2
    chair(B,'F2.bal.ch1',-0.62,4.75,180,'DeckRail'); chair(B,'F2.bal.ch2',-0.62,6.90,0,'DeckRail')
    B.cyl('F2.bal.tbl',-0.55,5.85,0,0.55,0.25,'DeckRail')
    plant(B,'F2.bal.pl',-0.95,7.45,0.7)
    # furniture
    bed(B,'F2.mh.bed',1.90,5.65,1.60,2.00,'y')
    B.box('F2.mh.ns1',(1.45,1.85),(7.24,7.64),(0,0.45),'WoodFurn')
    B.box('F2.mh.ns2',(3.55,3.95),(7.24,7.64),(0,0.45),'WoodFurn')
    wardrobe(B,'F2.mh.ward',0.45,3.45,4.70,5.18,2.15)
    B.box('F2.mh.dress',(0.36,0.91),(5.90,6.90),(0,0.90),'WoodFurn')
    rug(B,'F2.mh.rug',1.5,4.3,5.8,7.4)
    for i,y in enumerate([5.72,7.22]):
        B.box(f'F2.vh.sh{i}',(5.48,6.50),(y,y+0.42),(0,2.0),'Cabinet')
    B.box('F2.kph.vanity',(6.70,7.25),(6.00,7.30),(0,0.85),'Cabinet')
    B.cyl('F2.kph.s1',6.98,6.35,0.85,0.95,0.16,'Ceramic'); B.cyl('F2.kph.s2',6.98,6.95,0.85,0.95,0.16,'Ceramic')
    B.box('F2.kph.mirror',(6.67,6.70),(6.05,7.25),(1.1,1.9),'Glass')
    B.box('F2.kph.tray',(7.86,8.61),(6.87,7.62),(0,0.06),'Ceramic')
    B.box('F2.kph.gl1',(7.86,7.90),(6.87,7.62),(0,1.95),'Glass')
    B.box('F2.kph.gl2',(7.86,8.61),(6.87,6.91),(0,1.95),'Glass')
    B.cyl('F2.kph.shpole',8.45,7.45,0,2.05,0.02,'Metal')
    toilet(B,'F2.kph.wc',8.30,5.98,'N')
    B.box('F2.aula.console',(4.90,6.10),(0.36,0.78),(0,0.80),'WoodFurn')
    B.box('F2.aula.mirror',(5.10,5.90),(0.32,0.35),(0.9,1.8),'Glass')
    B.box('F2.aula.daybed',(6.45,7.25),(1.30,3.10),(0.15,0.45),'SofaWhite')
    plant(B,'F2.aula.pl1',4.05,0.65,0.9); plant(B,'F2.aula.pl2',8.10,4.60,1.0)
    bed(B,'F2.sw.bed',0.45,0.40,1.00,2.00,'x')
    table(B,'F2.sw.desk',2.70,3.45,1.60,2.80,0.74); chair(B,'F2.sw.ch',2.30,2.20,270)
    wardrobe(B,'F2.sw.ward',0.42,1.37,3.02,3.87,2.10)   # west corner, clear of the door
    rug(B,'F2.sw.rug',0.6,3.4,0.5,3.5)
    bed(B,'F2.se.bed',9.60,0.45,1.00,2.00,'y')
    wardrobe(B,'F2.se.ward',9.70,10.60,3.02,3.87,2.10)  # east corner, clear of the door
    table(B,'F2.se.desk',8.55,9.45,0.36,0.96,0.74); chair(B,'F2.se.ch',9.00,1.30,180)
    rug(B,'F2.se.rug',7.6,10.5,0.5,3.5)
    B.zoff=0.0

# ================================================================= KATTO
def build_roof(B):
    B.floor='katto'
    yr=3.99
    def mz(y): return 7.70-abs(y-yr)/3.0
    B.roofquad('R.main.s',[(-0.45,-0.55,mz(-0.55)),(11.43,-0.55,mz(-0.55)),(11.43,yr,7.70),(-0.45,yr,7.70)],0.15,'Roof')
    B.roofquad('R.main.n',[(-0.45,yr,7.70),(11.43,yr,7.70),(11.43,8.53,mz(8.53)),(-0.45,8.53,mz(8.53))],0.15,'Roof')
    for nm,gx in [('w',0.125),('e',10.855)]:
        B.prism(f'R.gable.{nm}',gx-0.125,gx+0.125,
                [(0.0,5.59),(7.98,5.59),(7.98,6.22),(3.99,7.55),(0.0,6.22)],'WallExt2',axis='x')
    # wing roof 1:7, fold +139.03 (z3.13), S edge +138.21 (z2.31)
    # wing: flat interior ceiling + shallow angled gable roof above (1:8, fold at y=4.68)
    B.slab('R.wing.ceil',[(11.00,0.10),(14.18,0.10),(14.18,3.40),(16.88,3.40),(16.88,7.88),(11.00,7.88)],2.60,2.66,'WallInt')
    B.roofquad('R.wing.s',[(10.98,-0.44,2.66),(17.42,-0.44,2.66),(17.42,4.68,3.30),(10.98,4.68,3.30)],0.12,'Roof')
    B.roofquad('R.wing.n',[(10.98,4.68,3.30),(17.42,4.68,3.30),(17.42,8.42,2.72),(10.98,8.42,2.72)],0.12,'Roof')
    B.prism('R.band.e',16.68,16.98,
            [(3.30,2.60),(7.98,2.60),(7.98,2.67),(4.68,3.18),(3.30,3.01)],'WallExt',axis='x')
    B.prism('R.band.din',14.10,14.40,
            [(0.0,2.60),(3.48,2.60),(3.48,3.03)],'WallExt',axis='x')
    B.box('R.band.notch',(14.28,16.98),(3.15,3.45),(2.60,3.01),'WallExt')
    B.box('R.band.n',(10.98,16.98),(7.68,7.98),(2.60,2.67),'WallExt')
    B.box('R.fascia.s',(10.98,17.42),(-0.44,-0.38),(2.40,2.54),'WallExt')
    B.box('R.fascia.e',(17.36,17.42),(-0.44,8.42),(2.40,2.54),'WallExt')
    B.box('R.fascia.n',(10.98,17.42),(8.36,8.42),(2.40,2.60),'WallExt')
    B.box('R.band.blk',(10.84,10.975),(0.0,7.98),(2.56,3.00),'WallInt')
    B.cyl('R.chimney',11.23,5.90,2.55,8.24,0.16,'TVBlack')     # round black steel flue
    B.cyl('R.chimcap',11.23,5.90,8.24,8.34,0.24,'TVBlack')

# ================================================================= AUTOKATOS/TR
def build_katos(B):
    B.floor='katos'
    # Carport west of the house across the driveway; open gable mouth faces the
    # street (north), ridge front-to-back, boat inside (street-view photo)
    X0,X1 = -0.50,9.00           # 9.5 long (x, N-S)
    Y0,Y1 = -8.90,-5.10          # 3.8 wide (y); east side faces the driveway
    zf=-0.55; hw=3.05            # bay slab +135.35 (asema: KATOS), wall tops unchanged
    Xv=5.50                      # VAR storage = rear (south) 3.5 m
    B.slab('TR.slab',[(X0,Y0),(X1,Y0),(X1,Y1),(X0,Y1)],zf-0.15,zf,'Concrete')
    B.slab('TR.varfloor',[(Xv,Y0),(X1,Y0),(X1,Y1),(Xv,Y1)],zf,-0.05,'Concrete')   # VAR floor +135.85 (asema: TR)
    # gray brick paving sloping per asema: street +135.10 -> carport/entry +135.30..35
    B.roofquad('TR.drive.slab.a',[(-3.00,-8.90,-0.78),(-0.45,-8.90,-0.70),(-0.45,-0.30,-0.70),(-3.00,-0.30,-0.78)],0.06,'Paver')
    B.roofquad('TR.drive.slab.b',[(-0.45,-5.10,-0.70),(9.30,-5.10,-0.55),(9.30,-3.40,-0.55),(-0.45,-3.40,-0.70)],0.06,'Paver')
    B.roofquad('TR.drive.slab.c',[(-0.45,-3.40,-0.70),(6.00,-3.40,-0.60),(6.00,-0.30,-0.60),(-0.45,-0.30,-0.70)],0.06,'Paver')
    wall_y(B,'TR.wW',Y0+0.06,X0,X1,zf,hw,0.12,mat='WallExt2')            # west long wall
    wall_x(B,'TR.wS',X1-0.06,Y0,Y1,zf,hw,0.12,
           ops=[W('win',-7.50,-6.70,1.40,2.40)],mat='WallExt2')          # rear gable wall + VAR window (TR sheet)
    wall_x(B,'TR.var.n',Xv+0.06,Y0,Y1,zf,hw,0.12,mat='WallExt2')         # VAR front wall
    wall_y(B,'TR.var.e',Y1-0.06,Xv,X1,zf,hw,0.12,
           ops=[W('door',5.80,6.70,0,2.55)],mat='WallExt2')              # VAR door to driveway
    # open bay: corner posts + partial white louver screen at the front-east
    for i,(px,py) in enumerate([(X0+0.02,Y1-0.18),(X0+0.02,Y0+0.06),(2.72,Y1-0.18)]):
        B.box(f'TR.post{i}',(px,px+0.14),(py,py+0.14),(zf,zf+3.00),'WoodFurn')
    zz=zf+0.10; i=0
    while zz<zf+2.70:
        B.box(f'TR.screen.b{i}',(X0+0.16,2.70),(Y1-0.12,Y1-0.05),(zz,zz+0.15),'SlatGray'); zz+=0.30; i+=1
    B.room('Room_katos_VAR',[(Xv+0.12,Y0+0.12),(X1-0.12,Y0+0.12),(X1-0.12,Y1-0.12),(Xv+0.12,Y1-0.12)],'ConcreteF',z=-0.05)
    B.room('Room_katos_AUTOKATOS',[(X0+0.07,Y0+0.12),(Xv,Y0+0.12),(Xv,Y1-0.05),(X0+0.07,Y1-0.05)],'ConcreteF',z=zf)
    # gable roof: ridge along x at y=-7.00, eave z2.76, ridge z3.62 (under-roof faces)
    B.roofquad('TR.roof.w',[(X0-0.30,Y0-0.30,2.88),(X1+0.30,Y0-0.30,2.88),(X1+0.30,-7.00,3.74),(X0-0.30,-7.00,3.74)],0.12,'Roof')
    B.roofquad('TR.roof.e',[(X0-0.30,-7.00,3.74),(X1+0.30,-7.00,3.74),(X1+0.30,Y1+0.30,2.88),(X0-0.30,Y1+0.30,2.88)],0.12,'Roof')
    for nm,gx in [('n',X0-0.18),('s',X1+0.18)]:
        B.prism(f'TR.gable.{nm}',gx-0.06,gx+0.06,
                [(Y0,zf+hw),(Y1,zf+hw),(Y1,2.76),(-7.00,3.62),(Y0,2.76)],'WallExt2',axis='x')
    # car parked inside the carport bay
    B.box('TR.car.body',(0.35,4.65),(-7.90,-6.10),(zf+0.30,zf+0.95),'Appliance')
    B.box('TR.car.cab',(1.15,3.85),(-7.70,-6.30),(zf+0.95,zf+1.45),'Glass')
    for i,(cx,cy) in enumerate([(1.25,-7.85),(3.75,-7.85),(1.25,-6.15),(3.75,-6.15)]):
        B.box(f'TR.car.wh{i}',(cx-0.31,cx+0.31),(cy-0.12,cy+0.12),(zf,zf+0.58),'TVBlack')
    B.floor='katos'
    for i,ty in enumerate([4.6,5.6,6.6,7.4]):
        B.cyl(f'TR.thuja{i}.tr',-1.05,ty,-0.78,-0.28,0.06,'WoodFurn')
        B.sph(f'TR.thuja{i}.fa',-1.05,ty,0.07,0.34,'Plant')
        B.sph(f'TR.thuja{i}.fb',-1.05,ty,0.57,0.24,'Plant')

# ================================================================= VALOT
def light(B,nm,x,y,z,kind='ceil'):
    if kind=='ceil':   B.cyl(nm,x,y,z-0.028,z,0.072,'LightOff',12)
    elif kind=='spot': B.cyl(nm,x,y,z-0.038,z,0.042,'LightOff',10)
    elif kind=='pend':
        B.cyl(nm+'.cord',x,y,z+0.10,z+0.52,0.008,'Metal')
        B.cyl(nm,x,y,z-0.05,z+0.10,0.115,'LightOff',14)
    elif kind=='wall_s': B.box(nm,(x-0.05,x+0.05),(y-0.05,y),(z-0.08,z+0.08),'LightOff')
    elif kind=='wall_ny':B.box(nm,(x-0.05,x+0.05),(y,y+0.05),(z-0.08,z+0.08),'LightOff')
    elif kind=='wall_w': B.box(nm,(x-0.05,x),(y-0.05,y+0.05),(z-0.08,z+0.08),'LightOff')
    elif kind=='boll':
        B.cyl(nm+'.pole',x,y,z,z+0.72,0.034,'Metal',10)
        B.cyl(nm,x,y,z+0.72,z+0.86,0.05,'LightOff',10)

def build_lights(B):
    # 1. krs (from '1 krs valaistus' drawing)
    B.floor='1krs'; L=lambda nm,x,y,z,k='ceil': light(B,nm,x,y,z,k)
    L('Light_1krs_LH',1.40,6.60,2.30);      L('Light_1krs_PH',3.20,6.60,2.42)
    L('Light_1krs_KHH_1',5.40,7.25,2.44);   L('Light_1krs_KHH_2',6.40,7.25,2.44)
    L('Light_1krs_VH',8.70,6.60,2.44);      L('Light_1krs_PORRAS',9.80,6.60,2.48)
    L('Light_1krs_WC',3.30,4.60,2.44);      L('Light_1krs_TEKN',1.20,4.50,2.44)
    L('Light_1krs_ET',5.60,3.20,2.48)
    L('Light_1krs_MH',2.10,2.00,2.48);      L('Light_1krs_TK',5.50,0.90,2.48)
    L('Light_1krs_VH2',6.70,0.95,2.44)
    L('Light_1krs_KT_1',8.35,2.00,2.50,'spot'); L('Light_1krs_KT_2',8.35,3.30,2.50,'spot')
    L('Light_1krs_KT_3',10.24,3.78,2.05,'pend')   # pendant over the island (hood covers the hob)
    L('Light_1krs_RUOKAILU',12.60,2.00,1.95,'pend')
    L('Light_1krs_OH_1',13.10,6.00,2.54,'spot'); L('Light_1krs_OH_2',14.50,6.00,2.54,'spot')
    L('Light_1krs_OH_3',15.80,6.00,2.54,'spot'); L('Light_1krs_OH_4',12.30,5.20,2.54,'spot')
    L('Light_1krs_OH_5',16.50,5.10,2.54,'spot')
    L('Light_ulko_etuovi_1',4.55,-0.02,2.15,'wall_s'); L('Light_ulko_etuovi_2',6.85,-0.02,2.15,'wall_s')
    L('Light_ulko_tekn',-0.02,3.85,2.15,'wall_w')
    L('Light_ulko_terassi_1',12.20,-0.02,2.30,'wall_s'); L('Light_ulko_terassi_2',13.80,-0.02,2.30,'wall_s')
    # 2. krs
    B.floor='2krs'; z2=Z_2+2.50
    L('Light_2krs_MH',2.60,6.20,z2);        L('Light_2krs_VH',5.95,6.60,z2)
    L('Light_2krs_KPH_1',7.10,6.30,z2,'spot'); L('Light_2krs_KPH_2',8.20,7.10,z2,'spot')
    L('Light_2krs_AULA_1',5.30,3.30,z2);    L('Light_2krs_AULA_2',7.30,4.70,z2)
    L('Light_2krs_AULA_3',4.50,1.00,z2);    L('Light_2krs_PORRAS',9.80,6.50,z2)
    L('Light_2krs_MH2',2.00,2.00,z2);       L('Light_2krs_MH3',9.00,2.00,z2)
    L('Light_ulko_parveke',-0.02,6.90,Z_2+2.15,'wall_w')
    # kellari
    B.floor='kellari'; zk=Z_K+2.42
    L('Light_kellari_VAR1_1',3.00,4.00,zk); L('Light_kellari_VAR1_2',7.50,4.00,zk)
    L('Light_kellari_WC',1.20,6.75,zk)
    L('Light_kellari_VAR2_1',12.50,4.50,zk);L('Light_kellari_VAR2_2',15.50,5.50,zk)
    # autokatos + piha
    B.floor='katos'
    L('Light_katos_1',1.60,-7.00,2.28);     L('Light_katos_2',4.20,-7.00,2.28)
    L('Light_katos_VAR',7.20,-7.00,2.30)
    L('Light_ulko_katos',6.25,-5.10,2.10,'wall_ny')
    B.floor='terassi'
    L('Light_ulko_piha_1',12.50,-4.05,-3.00,'boll'); L('Light_ulko_piha_2',14.50,-4.05,-3.00,'boll')
    L('Light_ulko_piha_3',16.40,-4.05,-3.00,'boll')

def build_all(B):
    build_kellari(B); build_krs1(B); build_krs2(B); build_roof(B); build_katos(B); build_lights(B)
