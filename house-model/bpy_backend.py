# Blender backend for the Marmorikatu house spec. Runs INSIDE Blender.
# Usage (from Blender):  exec(open(BASE+'/bpy_backend.py').read()); hk_run(BASE)
import bpy, bmesh, math, sys, os
from mathutils import Vector

MATS = {  # name: (hex, rough, metallic, alpha)
 'WallExt':('B3A292',0.80,0,1),'WallExt2':('B9AB9C',0.80,0,1),'WallInt':('F4F2EC',0.90,0,1),
 'DarkWood':('3B3734',0.70,0,1),
 'ConcreteW':('9A968F',0.90,0,1),'Concrete':('B5B2AC',0.95,0,1),'ConcreteF':('C9C6C0',0.95,0,1),
 'Wood':('E0D9CE',0.55,0,1),'Tile':('DEE3E6',0.25,0,1),'TileDark':('AEB6BA',0.30,0,1),
 'Deck':('6E655E',0.75,0,1),'DeckRail':('C4A484',0.70,0,1),'Roof':('3E4348',0.50,0,1),
 'Glass':('BFD9E8',0.08,0,0.30),'Door':('E9E7E1',0.60,0,1),'StairWood':('C7A06B',0.50,0,1),
 'Railing':('C8D4DA',0.20,0,0.45),'Chimney':('77726D',0.90,0,1),'SaunaWood':('D6B183',0.60,0,1),
 'Metal':('A8ADB2',0.35,0.8,1),'Ceramic':('F7F6F2',0.15,0,1),'Counter':('44464A',0.35,0,1),
 'Cabinet':('F0EEE8',0.50,0,1),'CabinetDark':('565A5E',0.50,0,1),'Appliance':('C6C9CC',0.30,0.6,1),
 'WoodFurn':('B58B5E',0.55,0,1),'BedWhite':('F4F2EA',0.85,0,1),'SofaWhite':('EDEAE2',0.90,0,1),
 'SofaGreen':('7A8F6E',0.90,0,1),'FabricBlue':('7C8FA0',0.90,0,1),'Rug':('CDD3D6',0.98,0,1),
 'Plant':('5E7F52',0.85,0,1),'Pot':('8B8E92',0.70,0,1),'TVBlack':('1E2022',0.40,0,1),
 'Slat':('6B5136',0.70,0,1),'SlatGray':('E3E4E0',0.65,0,1),'Rattan':('4A4B4D',0.85,0,1),
 'LightOff':('F1EFE8',0.35,0,1),'Paver':('9C9C9A',0.85,0,1),
 'Block':('7E7F80',0.90,0,1),'Soil':('6E5B48',0.95,0,1),
 'White':('F2F2EF',0.60,0,1),'Canopy':('EDF2F4',0.25,0,0.35),
}
def hexrgb(h):
    return tuple(int(h[i:i+2],16)/255 for i in (0,2,4))
def srgb2lin(c):
    return tuple((v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4) for v in c)

FLOORCOL = {'kellari':'Kellari','1krs':'Krs1','terassi':'Terassi','2krs':'Krs2','katto':'Katto','katos':'Katos'}
CATS = ['seinat_ulko','seinat_sisa','lasit','ovet','lattia','huoneet','portaat','kalusteet','valot']

class BlenderB:
    def __init__(self, base=None):
        self.floor=None; self.zoff=0.0; self.base=base
        self.mats={}; self.cols={}; self.emp={}; self.count=0
        scn=bpy.context.scene
        self.root=bpy.data.objects.new('Talo',None); scn.collection.objects.link(self.root)
        plank=None; paver=None; lattia=None
        if base:
            try: plank=bpy.data.images.load(base+'/seina_planks.png', check_existing=True)
            except Exception: plank=None
            try: paver=bpy.data.images.load(base+'/kiveys_pavers.png', check_existing=True)
            except Exception: paver=None
            try: lattia=bpy.data.images.load(base+'/lattia_tammi.png', check_existing=True)
            except Exception: lattia=None
        for m,(hx,rf,mt,al) in MATS.items():
            mat=bpy.data.materials.new(m); mat.use_nodes=True
            b=mat.node_tree.nodes.get('Principled BSDF')
            b.inputs['Base Color'].default_value=(*srgb2lin(hexrgb(hx)),al)
            b.inputs['Roughness'].default_value=rf; b.inputs['Metallic'].default_value=mt
            if al<1:
                b.inputs['Alpha'].default_value=al
                try: mat.blend_method='BLEND'
                except Exception: pass
                try: mat.surface_render_method='BLENDED'
                except Exception: pass
            if plank and m in ('WallExt','WallExt2'):
                tex=mat.node_tree.nodes.new('ShaderNodeTexImage'); tex.image=plank
                mat.node_tree.links.new(tex.outputs['Color'], b.inputs['Base Color'])
            if paver and m=='Paver':
                tex=mat.node_tree.nodes.new('ShaderNodeTexImage'); tex.image=paver
                mat.node_tree.links.new(tex.outputs['Color'], b.inputs['Base Color'])
            if lattia and m=='Wood':
                tex=mat.node_tree.nodes.new('ShaderNodeTexImage'); tex.image=lattia
                mat.node_tree.links.new(tex.outputs['Color'], b.inputs['Base Color'])
            self.mats[m]=mat
        for f,cn in FLOORCOL.items():
            col=bpy.data.collections.new(cn); scn.collection.children.link(col); self.cols[f]=col
            e=bpy.data.objects.new(cn,None); col.objects.link(e); e.parent=self.root; self.emp[f]=e
    def _cat(self,name,mat):
        n=name.lower()
        if name.startswith('Light_'): return 'valot'
        if self.floor=='katto': return None
        if mat=='Glass' or '.glass' in n: return 'lasit'
        if mat=='Door' or '.leaf' in n: return 'ovet'
        if mat in('WallExt','WallExt2','ConcreteW') : return 'seinat_ulko'
        if mat=='WallInt': return 'seinat_sisa'
        if name.startswith('Room_'): return 'huoneet'
        if '.slab' in n or '.deck' in n or '.balc' in n or '.porch' in n or '.stoop' in n: return 'lattia'
        if '.st' in n and ('sta' in n or '.step' in n) or '.stA' in name or '.stB' in name or '.stLand' in name or '.exstair' in n: return 'portaat'
        return 'kalusteet'
    def _add(self,name,mesh,mat):
        if mat in ('WallExt','WallExt2','Paver','Wood'):
            try:
                uvl=mesh.uv_layers.new()
                for li,l in enumerate(mesh.loops):
                    co=mesh.vertices[l.vertex_index].co
                    if mat=='Paver':  uvl.data[li].uv=(co.x*0.5, co.y*0.5)
                    elif mat=='Wood': uvl.data[li].uv=(co.x*0.42, co.y*0.42)
                    else:             uvl.data[li].uv=((co.x+co.y)*0.4, co.z*0.4)
            except Exception: pass
        obj=bpy.data.objects.new(name,mesh)
        obj.data.materials.append(self.mats[mat])
        col=self.cols[self.floor]; col.objects.link(obj)
        cat=self._cat(name,mat)
        if cat is None: obj.parent=self.emp[self.floor]
        else:
            key=(self.floor,cat)
            if key not in self.emp:
                e=bpy.data.objects.new(f'{FLOORCOL[self.floor]}_{cat}',None)
                col.objects.link(e); e.parent=self.emp[self.floor]; self.emp[key]=e
            obj.parent=self.emp[key]
        self.count+=1
        return obj
    def box(self,name,xs,ys,zs,mat):
        z0,z1=zs[0]+self.zoff,zs[1]+self.zoff
        x0,x1=min(xs),max(xs); y0,y1=min(ys),max(ys)
        if x1-x0<1e-4 or y1-y0<1e-4 or z1-z0<1e-4: return
        me=bpy.data.meshes.new(name)
        v=[(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
        f=[(0,1,2,3),(7,6,5,4),(0,4,5,1),(1,5,6,2),(2,6,7,3),(3,7,4,0)]
        me.from_pydata(v,[],f); me.validate(); me.update()
        self._add(name,me,mat)
    def cyl(self,name,x,y,z0,z1,r,mat,segs=20):
        z0+=self.zoff; z1+=self.zoff
        me=bpy.data.meshes.new(name); bm=bmesh.new()
        try:
            bmesh.ops.create_cone(bm,cap_ends=True,segments=segs,radius1=r,radius2=r,depth=z1-z0)
        except TypeError:
            bmesh.ops.create_cone(bm,cap_ends=True,segments=segs,diameter1=r,diameter2=r,depth=z1-z0)
        bmesh.ops.translate(bm,verts=bm.verts,vec=(x,y,(z0+z1)/2))
        bm.to_mesh(me); bm.free(); self._add(name,me,mat)
    def sph(self,name,x,y,z,r,mat):
        me=bpy.data.meshes.new(name); bm=bmesh.new()
        try:
            bmesh.ops.create_uvsphere(bm,u_segments=12,v_segments=8,radius=r)
        except TypeError:
            bmesh.ops.create_uvsphere(bm,u_segments=12,v_segments=8,diameter=r)
        bmesh.ops.translate(bm,verts=bm.verts,vec=(x,y,z+self.zoff))
        bm.to_mesh(me); bm.free(); self._add(name,me,mat)
    def _prism(self,name,pts3_bottom,vec,mat):
        me=bpy.data.meshes.new(name); bm=bmesh.new()
        vs=[bm.verts.new(p) for p in pts3_bottom]
        try: face=bm.faces.new(vs)
        except ValueError: bm.free(); return
        r=bmesh.ops.extrude_face_region(bm,geom=[face])
        verts=[g for g in r['geom'] if isinstance(g,bmesh.types.BMVert)]
        bmesh.ops.translate(bm,verts=verts,vec=vec)
        bmesh.ops.recalc_face_normals(bm,faces=bm.faces)
        bm.to_mesh(me); bm.free(); self._add(name,me,mat)
    def slab(self,name,poly,z0,z1,mat,holes=None):
        self._prism(name,[(p[0],p[1],z0+self.zoff) for p in poly],(0,0,z1-z0),mat)
    def room(self,name,poly,mat,z=0.0,holes=None):
        self._prism(name,[(p[0],p[1],z+0.012) for p in poly],(0,0,0.008),mat)
    def roofquad(self,name,pts,thick,mat):
        self._prism(name,[(p[0],p[1],p[2]) for p in pts],(0,0,-thick),mat)
    def prism(self,name,x0,x1,poly_yz,mat,axis='x'):
        if axis=='x': self._prism(name,[(x0,a,b) for (a,b) in poly_yz],(x1-x0,0,0),mat)
        else:         self._prism(name,[(a,x0,b) for (a,b) in poly_yz],(0,x1-x0,0),mat)

def hk_clear():
    for o in list(bpy.data.objects): bpy.data.objects.remove(o,do_unlink=True)
    for blocks in (bpy.data.meshes,bpy.data.materials,bpy.data.collections,bpy.data.cameras,bpy.data.lights):
        for b in list(blocks):
            try: blocks.remove(b)
            except Exception: pass

def hk_lightcam():
    scn=bpy.context.scene
    sun=bpy.data.lights.new('Sun','SUN'); sun.energy=3.0; sun.angle=math.radians(5)
    so=bpy.data.objects.new('Sun',sun); scn.collection.objects.link(so)
    so.rotation_euler=(math.radians(50),0,math.radians(-35))
    cam=bpy.data.cameras.new('Cam'); co=bpy.data.objects.new('Cam',cam)
    scn.collection.objects.link(co); scn.camera=co
    co.location=(28,-16,16); co.rotation_euler=(math.radians(60),0,math.radians(55))
    scn.world=bpy.data.worlds.new('World'); scn.world.use_nodes=True
    bg=scn.world.node_tree.nodes.get('Background')
    bg.inputs[0].default_value=(0.85,0.87,0.90,1); bg.inputs[1].default_value=0.5

def hk_run(base):
    if base not in sys.path: sys.path.insert(0,base)
    import importlib
    if 'spec' in sys.modules: spec=importlib.reload(sys.modules['spec'])
    else: import spec
    hk_clear()
    B=BlenderB(base)
    spec.build_all(B)
    hk_lightcam()
    return f'built {B.count} objects'

def hk_export(base):
    bpy.ops.wm.save_as_mainfile(filepath=base+'/marmorikatu.blend')
    try:
        bpy.ops.export_scene.gltf(filepath=base+'/marmorikatu-house.glb',export_format='GLB',
            export_apply=True,export_cameras=False,export_lights=False,export_yup=True)
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=base+'/marmorikatu-house.glb',export_format='GLB',
            export_apply=True)
    hk_export_usdz(base)
    return 'exported'

def hk_export_usdz(base, forward='NEGATIVE_Z'):   # NEGATIVE_Z == glTF/cameras.json frame (verified with usd-core)
    """USDZ for Apple/SceneKit (Kotlin/Native platform.SceneKit). Goal: a natively
    Y-up stage, meters, world coords identical to the GLB / cameras.json frame."""
    usdz=base+'/marmorikatu-house.usdz'
    attempts=[
        dict(filepath=usdz,convert_orientation=True,export_global_up_selection='Y',
             export_global_forward_selection=forward,selected_objects_only=False,
             export_animation=False,export_lights=False,export_cameras=False),
        dict(filepath=usdz,convert_orientation=True,export_global_up_selection='Y',
             export_global_forward_selection=forward,selected_objects_only=False),
        dict(filepath=usdz,selected_objects_only=False),
        dict(filepath=usdz),
    ]
    for kw in attempts:
        try:
            bpy.ops.wm.usd_export(**kw)
            return 'usdz: '+str(sorted(kw.keys()))
        except TypeError:
            continue
    return 'usdz export failed'
