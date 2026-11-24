"""
Legacy Package IMPORTER  —  jalankan di Blender LAMA (3.x; juga jalan di 4.x/5.x)

Membangun ulang scene dari legacy package (hasil legacy_export.py) lalu
menyimpannya sebagai .blend versi Blender yang menjalankan script ini.

Cara pakai (command line):
    blender -b --factory-startup --python legacy_import.py -- PAKET_FOLDER_ATAU_ZIP OUTPUT.blend [--no-pack]

Atau install sebagai add-on:  File > Import > Legacy Package
"""

bl_info = {
    "name": "Legacy Package Import (dari Blender 4.x/5.x)",
    "author": "blend_downgrade",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > Legacy Package",
    "description": "Bangun ulang scene dari Legacy Package yang dibuat di Blender lebih baru",
    "category": "Import-Export",
}

import bpy
import os
import sys
import json
import math
import tempfile
import zipfile
import numpy as np
from mathutils import Matrix

_warnings = []
_skipped = {"count": 0}
VERBOSE = False

# nama datablock asli -> datablock yang dibuat
MAP = {}


def warn(msg):
    if msg not in _warnings:
        _warnings.append(msg)
        print("[legacy-import] PERINGATAN:", msg)


def remember(kind, name, idb):
    MAP.setdefault(kind, {})[name] = idb


def lookup(kind, name):
    if name is None:
        return None
    idb = MAP.get(kind, {}).get(name)
    if idb is None:
        coll = getattr(bpy.data, kind, None)
        idb = coll.get(name) if coll is not None else None
    return idb


def fj(v):
    """Nilai JSON -> nilai Blender."""
    if isinstance(v, dict):
        if "__id__" in v:
            return lookup(v["__id__"], v["name"])
        if "__set__" in v:
            return set(v["__set__"])
    return v


def rna_load(struct, props, skip=()):
    if not props:
        return
    rna_props = struct.bl_rna.properties
    for phase in (0, 1):
        for k, v in props.items():
            if k in skip:
                continue
            p = rna_props.get(k)
            if p is None:
                if phase == 1:
                    _skipped["count"] += 1
                continue
            if (phase == 0) != (p.type == "ENUM"):
                continue
            if isinstance(v, dict) and "__struct__" in v:
                sub = getattr(struct, k, None)
                if sub is not None:
                    rna_load(sub, v["__struct__"])
                continue
            try:
                val = fj(v)
                if isinstance(v, dict) and "__id__" in v and val is None:
                    continue
                setattr(struct, k, val)
            except Exception as e:
                _skipped["count"] += 1
                if VERBOSE:
                    print(f"  skip {struct!r}.{k} = {v!r}: {e}")


# ---------------------------------------------------------------- node trees

# Principled BSDF v2 (4.0+) -> v1 (3.x), dan sebaliknya
SOCKET_ALIASES = {
    "Subsurface Weight": ["Subsurface"],
    "Specular IOR Level": ["Specular"],
    "Transmission Weight": ["Transmission"],
    "Coat Weight": ["Clearcoat"],
    "Coat Roughness": ["Clearcoat Roughness"],
    "Coat Normal": ["Clearcoat Normal"],
    "Sheen Weight": ["Sheen"],
    "Emission Color": ["Emission"],
    "Subsurface": ["Subsurface Weight"],
    "Specular": ["Specular IOR Level"],
    "Transmission": ["Transmission Weight"],
    "Clearcoat": ["Coat Weight"],
    "Clearcoat Roughness": ["Coat Roughness"],
    "Clearcoat Normal": ["Coat Normal"],
    "Sheen": ["Sheen Weight"],
    "Emission": ["Emission Color"],
}


def find_socket(sockets, key, by_index=False):
    ident = key["identifier"]
    for s in sockets:
        if s.identifier == ident and s.identifier != "__extend__":
            return s
    for nm in [key["name"]] + SOCKET_ALIASES.get(key["name"], []):
        c = 0
        for s in sockets:
            if s.name == nm:
                if c == key["occ"]:
                    return s
                c += 1
    if by_index and key["index"] < len(sockets):
        s = sockets[key["index"]]
        if s.identifier != "__extend__":
            return s
    return None


def set_socket_value(sock, v):
    if not hasattr(sock, "default_value"):
        return
    cur = sock.default_value
    val = fj(v)
    try:
        n = len(cur)
    except TypeError:
        n = None
    if isinstance(val, list):
        if n is None:
            return  # mis. warna (4.x) -> float (3.x): beda arti, lewati
        if len(val) != n:
            val = (list(val) + [1.0] * n)[:n]
    elif n is not None and not isinstance(cur, str):
        return
    try:
        sock.default_value = val
    except Exception:
        _skipped["count"] += 1


def load_ramp(ramp, d):
    for k in ("color_mode", "interpolation", "hue_interpolation"):
        try:
            setattr(ramp, k, d[k])
        except Exception:
            pass
    els = sorted(d["elements"], key=lambda e: e["position"])
    if not els:
        return
    # elemen otomatis diurutkan ulang saat posisinya diubah, jadi sisakan satu
    # lalu tambahkan sisanya lewat elements.new() yang mengembalikan elemennya
    while len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])
    first = ramp.elements[0]
    first.position = els[0]["position"]
    first.color = els[0]["color"]
    for ed in els[1:]:
        e = ramp.elements.new(ed["position"])
        e.color = ed["color"]


def load_curvemap(m, d):
    for k in ("use_clip", "clip_min_x", "clip_min_y", "clip_max_x", "clip_max_y",
              "black_level", "white_level"):
        try:
            setattr(m, k, d[k])
        except Exception:
            pass
    for c, pts in zip(m.curves, d["curves"]):
        while len(c.points) > len(pts):
            c.points.remove(c.points[-1])
        while len(c.points) < len(pts):
            c.points.new(0.5, 0.5)
        for p, pd in zip(c.points, pts):
            p.location = pd["location"]
            try:
                p.handle_type = pd["handle_type"]
            except Exception:
                pass
    try:
        m.update()
    except Exception:
        pass


def build_tree(tree, data, owner):
    if data is None:
        return
    tree.nodes.clear()
    nodes = {}
    for nd in data["nodes"]:
        try:
            n = tree.nodes.new(nd["bl_idname"])
        except Exception:
            warn(f"Node '{nd['bl_idname']}' tidak ada di Blender {bpy.app.version_string} "
                 f"({owner}: '{nd['name']}') - dilewati")
            continue
        n.name = nd["name"]
        nodes[nd["name"]] = n
    for nd in data["nodes"]:
        n = nodes.get(nd["name"])
        if n is None:
            continue
        if nd.get("parent") in nodes:
            n.parent = nodes[nd["parent"]]
        n.label = nd["label"]
        n.width = nd["width"]
        n.hide = nd["hide"]
        n.mute = nd["mute"]
        if "color" in nd:
            n.use_custom_color = True
            n.color = nd["color"][:3]
        rna_load(n, nd["props"])
        if "color_ramp" in nd and getattr(n, "color_ramp", None) is not None:
            load_ramp(n.color_ramp, nd["color_ramp"])
        if "mapping" in nd and isinstance(getattr(n, "mapping", None), bpy.types.CurveMapping):
            load_curvemap(n.mapping, nd["mapping"])
        is_group_io = n.bl_idname in ("NodeGroupInput", "NodeGroupOutput")
        for coll, sd_list in ((n.inputs, nd["inputs"]), (n.outputs, nd["outputs"])):
            for sd in sd_list:
                s = find_socket(coll, sd, by_index=is_group_io)
                if s is None:
                    continue
                s.hide = sd.get("hide", False)
                if "value" in sd:
                    set_socket_value(s, sd["value"])
        n.location = nd["location"]
    for ld in data["links"]:
        a, b = nodes.get(ld["from_node"]), nodes.get(ld["to_node"])
        if a is None or b is None:
            continue
        sa = find_socket(a.outputs, ld["from_socket"],
                         by_index=a.bl_idname in ("NodeGroupInput", "ShaderNodeGroup"))
        sb = find_socket(b.inputs, ld["to_socket"],
                         by_index=b.bl_idname in ("NodeGroupOutput", "ShaderNodeGroup"))
        if sa is None or sb is None:
            warn(f"Link {ld['from_node']}.{ld['from_socket']['name']} -> "
                 f"{ld['to_node']}.{ld['to_socket']['name']} di {owner} tidak bisa dibuat")
            continue
        l = tree.links.new(sa, sb)
        if ld.get("muted") and hasattr(l, "is_muted"):
            l.is_muted = True


SUBTYPE_SUFFIX = {"FACTOR": "Factor", "PERCENTAGE": "Percentage", "ANGLE": "Angle",
                  "DISTANCE": "Distance", "TIME": "Time", "UNSIGNED": "Unsigned",
                  "DIRECTION": "Direction", "EULER": "Euler", "TRANSLATION": "Translation",
                  "VELOCITY": "Velocity", "ACCELERATION": "Acceleration", "XYZ": "XYZ"}


def build_interface(tree, items):
    for it in items:
        stype = it["socket_type"]
        sub = it.get("subtype", "NONE")
        if hasattr(tree, "interface"):  # 4.0+
            try:
                s = tree.interface.new_socket(it["name"], in_out=it["in_out"], socket_type=stype)
            except Exception as e:
                warn(f"Socket group '{it['name']}' ({stype}) gagal: {e}")
                continue
            if sub != "NONE" and hasattr(s, "subtype"):
                try:
                    s.subtype = sub
                except Exception:
                    pass
        else:  # 3.x
            coll = tree.inputs if it["in_out"] == "INPUT" else tree.outputs
            s = None
            for cand in ([stype + SUBTYPE_SUFFIX[sub]] if sub in SUBTYPE_SUFFIX else []) + [stype]:
                try:
                    s = coll.new(cand, it["name"])
                    break
                except Exception:
                    continue
            if s is None:
                warn(f"Socket group '{it['name']}' ({stype}) tidak didukung di versi ini")
                continue
        for k in ("min_value", "max_value", "default_value", "hide_value", "description"):
            if k in it and hasattr(s, k):
                try:
                    setattr(s, k, fj(it[k]))
                except Exception:
                    pass


# ---------------------------------------------------------------- images

COLORSPACE_FALLBACK = {
    "Linear Rec.709": ["Linear"], "Linear": ["Linear Rec.709"],
    "Linear CIE-XYZ D65": ["XYZ"], "Linear Rec.2020": ["Linear"],
    "AgX Base sRGB": ["sRGB"], "Non-Color": ["Non-Color", "Raw"],
}


def load_images(pkg, images):
    for d in images:
        if not d.get("file"):
            continue
        path = d["file"]
        try:
            path = pkg_path(pkg, d["file"])
            img = bpy.data.images.load(path, check_existing=False)
        except Exception as e:
            warn(f"Gagal memuat tekstur {path}: {e}")
            continue
        img.name = d["name"]
        cs = d.get("colorspace")
        for c in [cs] + COLORSPACE_FALLBACK.get(cs, []):
            try:
                img.colorspace_settings.name = c
                break
            except Exception:
                continue
        try:
            img.alpha_mode = d["alpha_mode"]
        except Exception:
            pass
        remember("images", d["name"], img)


# ---------------------------------------------------------------- meshes

def _set_edge_attr(me, prop, attr_name, dtype_name, values):
    try:
        me.edges.foreach_set(prop, values)
        return
    except Exception:
        pass
    a = me.attributes.get(attr_name) or me.attributes.new(attr_name, dtype_name, "EDGE")
    a.data.foreach_set("value", values)


def build_mesh(pkg, meta):
    A = np.load(pkg_path(pkg, meta["file"]), allow_pickle=False)
    me = bpy.data.meshes.new(meta["name"])
    co = A["co"]
    ev = A["edges"]
    lv = A["loop_vert"]
    ls = A["poly_start"]
    lt = A["poly_total"]
    me.vertices.add(len(co) // 3)
    me.vertices.foreach_set("co", co)
    me.edges.add(len(ev) // 2)
    me.edges.foreach_set("vertices", ev)
    me.loops.add(len(lv))
    me.loops.foreach_set("vertex_index", lv)
    edges_ok = True
    try:
        me.loops.foreach_set("edge_index", A["loop_edge"])
    except Exception:
        edges_ok = False
    me.polygons.add(len(ls))
    me.polygons.foreach_set("loop_start", ls)
    try:
        me.polygons.foreach_set("loop_total", lt)  # wajib di 3.x, read-only di 4.x
    except Exception:
        pass
    me.polygons.foreach_set("material_index", A["poly_mat"])
    me.update(calc_edges=not edges_ok)
    me.validate(clean_customdata=False)
    me.polygons.foreach_set("use_smooth", A["poly_smooth"])

    # urutan edge bisa berubah setelah validate: petakan lewat pasangan vertex
    ne_src = len(ev) // 2
    remap = None
    if len(me.edges) != ne_src or not edges_ok:
        cur = np.empty(len(me.edges) * 2, np.int32)
        me.edges.foreach_get("vertices", cur)
        lut = {}
        for i in range(len(me.edges)):
            a, b = cur[2 * i], cur[2 * i + 1]
            lut[(min(a, b), max(a, b))] = i
        remap = np.full(ne_src, -1, np.int64)
        for i in range(ne_src):
            a, b = ev[2 * i], ev[2 * i + 1]
            remap[i] = lut.get((min(a, b), max(a, b)), -1)

    def edge_arr(src, dtype):
        if remap is None:
            return src.astype(dtype)
        out = np.zeros(len(me.edges), dtype)
        ok = remap >= 0
        out[remap[ok]] = src[ok]
        return out

    has_sharp = "edge_sharp" in A.files
    if has_sharp:
        _set_edge_attr(me, "use_edge_sharp", "sharp_edge", "BOOLEAN", edge_arr(A["edge_sharp"], bool))
    if "edge_seam" in A.files:
        _set_edge_attr(me, "use_seam", "uv_seam", "BOOLEAN", edge_arr(A["edge_seam"], bool))
    if "edge_crease" in A.files:
        _set_edge_attr(me, "crease", "crease_edge", "FLOAT", edge_arr(A["edge_crease"], np.float32))
    if "edge_bevel" in A.files:
        try:
            me.use_customdata_edge_bevel = True
        except Exception:
            pass
        _set_edge_attr(me, "bevel_weight", "bevel_weight_edge", "FLOAT",
                       edge_arr(A["edge_bevel"], np.float32))
    if "vert_crease" in A.files:
        try:
            vc = me.vertex_creases.data if hasattr(me, "vertex_creases") and me.vertex_creases \
                else None
            if vc is None and hasattr(me, "vertex_creases"):
                me.vertex_creases_ensure()
                vc = me.vertex_creases.data
            if vc is not None:
                vc.foreach_set("value", A["vert_crease"])
            else:
                raise AttributeError
        except Exception:
            try:
                a = me.attributes.new("crease_vert", "FLOAT", "POINT")
                a.data.foreach_set("value", A["vert_crease"])
            except Exception:
                pass

    for i, uvd in enumerate(meta["uv_layers"]):
        uv = me.uv_layers.new(name=uvd["name"])
        uv.data.foreach_set("uv", A[f"uv_{i}"])
        if uvd["active"]:
            me.uv_layers.active = uv
        if uvd["active_render"]:
            uv.active_render = True

    for i, cd in enumerate(meta["color_attributes"]):
        arr = A[f"col_{i}"]
        try:
            ca = me.color_attributes.new(cd["name"], cd["data_type"], cd["domain"])
        except Exception:
            if cd["domain"] != "CORNER":
                warn(f"Color attribute '{cd['name']}' (per-vertex) tidak didukung versi ini")
                continue
            ca = me.vertex_colors.new(name=cd["name"])
        ca.data.foreach_set("color", arr)
        try:
            if cd.get("active"):
                me.color_attributes.active_color = ca
            if cd.get("render"):
                me.color_attributes.render_color_index = list(me.color_attributes).index(ca)
        except Exception:
            pass

    if meta.get("custom_normals"):
        if hasattr(me, "use_auto_smooth"):
            me.use_auto_smooth = True
            me.auto_smooth_angle = math.pi
        n = A["custom_normals"].reshape(-1, 3)
        try:
            me.normals_split_custom_set([tuple(x) for x in n])
        except Exception as e:
            warn(f"Custom normals '{me.name}' gagal: {e}")
    elif has_sharp and hasattr(me, "use_auto_smooth"):
        # di 3.x, sharp edge hanya berpengaruh kalau Auto Smooth aktif
        me.use_auto_smooth = True
        me.auto_smooth_angle = math.pi

    for mname in meta["materials"]:
        me.materials.append(lookup("materials", mname))
    return me, A


def apply_vertex_groups(obj, A):
    if "vg_vert" not in A.files:
        return
    vv, vgi, vw = A["vg_vert"], A["vg_group"], A["vg_weight"]
    for gi, vg in enumerate(obj.vertex_groups):
        sel = vgi == gi
        if not sel.any():
            continue
        verts, weights = vv[sel], vw[sel]
        for w in np.unique(weights):
            idx = verts[weights == w].tolist()
            vg.add(idx, float(w), "REPLACE")


def apply_shape_keys(obj, sk, A):
    if not sk:
        return
    blocks = []
    for i, bd in enumerate(sk["blocks"]):
        kb = obj.shape_key_add(name=bd["name"], from_mix=False)
        kb.data.foreach_set("co", A[f"sk_{i}"])
        blocks.append((kb, bd))
    key = obj.data.shape_keys
    key.use_relative = sk["use_relative"]
    kbs = key.key_blocks
    for kb, bd in blocks:
        for k in ("slider_min", "slider_max", "value", "mute", "vertex_group", "interpolation"):
            try:
                setattr(kb, k, bd[k])
            except Exception:
                pass
        if bd["relative_key"] in kbs:
            kb.relative_key = kbs[bd["relative_key"]]
    if sk.get("animation"):
        apply_anim(key, sk["animation"])


# ---------------------------------------------------------------- animation

def _new_fcurve(act, idb, path, index, group):
    fcs = getattr(act, "fcurves", None)
    if fcs is not None and hasattr(fcs, "new"):
        try:
            return fcs.new(path, index=index, action_group=group or "")
        except Exception:
            return fcs.find(path, index=index)
    # Blender 5.x (layered action)
    return act.fcurve_ensure_for_datablock(idb, path, index=index, group_name=group or "")


def apply_anim(idb, anim):
    if not anim or not anim.get("fcurves"):
        return
    ad = idb.animation_data_create()
    act = bpy.data.actions.new(anim["action"])
    ad.action = act
    for c in anim["fcurves"]:
        try:
            fc = _new_fcurve(act, idb, c["data_path"], c["index"], c["group"])
        except Exception as e:
            warn(f"F-curve {c['data_path']}[{c['index']}] di '{idb.name}' gagal: {e}")
            continue
        if fc is None:
            continue
        n = len(c["co"])
        kp = fc.keyframe_points
        kp.add(n)
        kp.foreach_set("co", np.array(c["co"], np.float32).ravel())
        for i, k in enumerate(kp):
            try:
                k.interpolation = c["interp"][i]
                k.easing = c["easing"][i]
                k.handle_left_type = c["hlt"][i]
                k.handle_right_type = c["hrt"][i]
            except Exception:
                pass
            k.handle_left = c["hl"][i]
            k.handle_right = c["hr"][i]
        try:
            fc.extrapolation = c["extrapolation"]
        except Exception:
            pass
        for mt in c.get("modifiers", []):
            fc.modifiers.new(mt)
        fc.update()


# ---------------------------------------------------------------- objects

def add_modifiers(obj, mods):
    for md in mods:
        try:
            m = obj.modifiers.new(md["name"], md["type"])
        except Exception as e:
            warn(f"Modifier {md['type']} di '{obj.name}' tidak didukung: {e}")
            continue
        rna_load(m, md["props"])


def add_constraints(owner, cons, label):
    for cd in cons:
        try:
            c = owner.constraints.new(cd["type"])
        except Exception as e:
            warn(f"Constraint {cd['type']} di '{label}' tidak didukung: {e}")
            continue
        c.name = cd["name"]
        rna_load(c, cd["props"])


def set_custom_props(idb, props):
    for k, v in (props or {}).items():
        try:
            idb[k] = v
        except Exception:
            pass


def build_armature(ad, scene):
    arm = bpy.data.armatures.new(ad["name"])
    for k in ("display_type", "show_names", "show_axes", "pose_position"):
        try:
            setattr(arm, k, ad[k])
        except Exception:
            pass
    tmp = bpy.data.objects.new("__tmp_arm__", arm)
    scene.collection.objects.link(tmp)
    vl = bpy.context.view_layer
    prev = vl.objects.active
    vl.objects.active = tmp
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = {}
    for b in ad["bones"]:
        eb = arm.edit_bones.new(b["name"])
        eb.head = b["head"]
        eb.tail = b["tail"]
        eb.matrix = Matrix(b["matrix"])
        for k in ("use_deform", "use_inherit_rotation", "inherit_scale", "use_local_location",
                  "bbone_segments", "envelope_distance", "head_radius", "tail_radius"):
            try:
                setattr(eb, k, b[k])
            except Exception:
                pass
        ebs[b["name"]] = (eb, b)
    for eb, b in ebs.values():
        if b["parent"] in ebs:
            eb.parent = ebs[b["parent"]][0]
            eb.use_connect = b["use_connect"]
    bpy.ops.object.mode_set(mode="OBJECT")
    vl.objects.active = prev
    bpy.data.objects.remove(tmp)
    for b in ad["bones"]:
        if b.get("hide") and b["name"] in arm.bones:
            arm.bones[b["name"]].hide = True
    bcs = ad.get("bone_collections") or []
    if bcs and hasattr(arm, "collections"):  # Blender 4.0+
        made = {}
        for cd in bcs:
            parent = made.get(cd.get("parent"))
            try:
                c = arm.collections.new(cd["name"], parent=parent) if parent \
                    else arm.collections.new(cd["name"])
            except TypeError:  # 4.0: tanpa hierarki
                c = arm.collections.new(cd["name"])
            c.is_visible = cd.get("is_visible", True)
            made[cd["name"]] = c
        for b in ad["bones"]:
            for cname in b.get("collections", []):
                if cname in made and b["name"] in arm.bones:
                    made[cname].assign(arm.bones[b["name"]])
    elif len(bcs) > 1:
        warn(f"Bone collections di '{ad['name']}' tidak ada di Blender "
             f"{bpy.app.version_string}; semua bone ditaruh di layer 1")
    return arm


def pkg_path(pkg, rel):
    """Path file di dalam paket. Tolak path absolut / '..' yang keluar dari paket,
    supaya paket dari orang lain tidak bisa menarik file lain di komputer ini."""
    if not isinstance(rel, str) or os.path.isabs(rel):
        raise ValueError(f"Path tidak valid di paket: {rel!r}")
    root = os.path.realpath(pkg)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep):
        raise ValueError(f"Path keluar dari folder paket: {rel!r}")
    return full


def import_package(pkg, out_path=None, pack=True, clear_scene=True):
    _warnings.clear()
    _skipped["count"] = 0
    MAP.clear()
    if pkg.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="legacy_pkg_")
        with zipfile.ZipFile(pkg) as z:
            z.extractall(tmp)
        pkg = tmp
    with open(os.path.join(pkg, "scene.json"), encoding="utf-8") as f:
        D = json.load(f)
    print(f"[legacy-import] Paket dari Blender {D.get('source_version')} -> "
          f"membangun di Blender {bpy.app.version_string}")

    if clear_scene:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene

    load_images(pkg, D["images"])

    # node group: shell + interface dulu, isi node belakangan (agar group bertingkat aman)
    for g in D["node_groups"]:
        ng = bpy.data.node_groups.new(g["name"], "ShaderNodeTree")
        remember("node_groups", g["name"], ng)
        build_interface(ng, g["interface"])
    for g in D["node_groups"]:
        build_tree(lookup("node_groups", g["name"]), g["tree"], "node group " + g["name"])

    for md in D["materials"]:
        mat = bpy.data.materials.new(md["name"])
        remember("materials", md["name"], mat)
    for md in D["materials"]:
        mat = lookup("materials", md["name"])
        rna_load(mat, md["props"], skip={"use_nodes"})
        if md["tree"]:
            try:
                mat.use_nodes = True
            except Exception:
                pass
            build_tree(mat.node_tree, md["tree"], "material " + md["name"])
        set_custom_props(mat, md.get("custom_props"))

    for wd in D["worlds"]:
        w = bpy.data.worlds.new(wd["name"])
        remember("worlds", wd["name"], w)
        w.color = wd["color"]
        if wd["tree"]:
            try:
                w.use_nodes = True
            except Exception:
                pass
            build_tree(w.node_tree, wd["tree"], "world " + wd["name"])

    for cd in D["cameras"]:
        cam = bpy.data.cameras.new(cd["name"])
        remember("cameras", cd["name"], cam)
    for ld in D["lights"]:
        lt = bpy.data.lights.new(ld["name"], ld["type"])
        remember("lights", ld["name"], lt)
        if ld.get("tree"):
            lt.use_nodes = True
            build_tree(lt.node_tree, ld["tree"], "light " + ld["name"])
    for ad in D["armatures"]:
        remember("armatures", ad["name"], build_armature(ad, scene))

    mesh_cache = {}
    for key, meta in D["meshes"].items():
        mesh_cache[key] = build_mesh(pkg, meta)
        remember("meshes", key, mesh_cache[key][0])

    # collections
    for cd in D["collections"]:
        c = bpy.data.collections.new(cd["name"])
        remember("collections", cd["name"], c)
    for cd in D["collections"]:
        c = lookup("collections", cd["name"])
        for ch in cd["children"]:
            chc = lookup("collections", ch)
            if chc is not None and chc.name not in c.children:
                c.children.link(chc)
        for k in ("hide_viewport", "hide_render", "hide_select", "instance_offset"):
            try:
                setattr(c, k, cd[k])
            except Exception:
                pass
    for ch in D["scene"]["children"]:
        chc = lookup("collections", ch)
        if chc is not None:
            scene.collection.children.link(chc)

    # objek: buat dulu semuanya
    weighted_meshes = set()
    for od in D["objects"]:
        data = None
        if od["type"] == "MESH":
            data = lookup("meshes", od["data"])
        elif od["type"] == "ARMATURE":
            data = lookup("armatures", od["data"])
        elif od["type"] == "CAMERA":
            data = lookup("cameras", od["data"])
        elif od["type"] == "LIGHT":
            data = lookup("lights", od["data"])
        obj = bpy.data.objects.new(od["name"], data)
        remember("objects", od["name"], obj)
        linked = False
        for cn in od["collections"]:
            c = scene.collection if cn == "__scene__" else lookup("collections", cn)
            if c is not None:
                c.objects.link(obj)
                linked = True
        if not linked:
            scene.collection.objects.link(obj)

    # lalu properti, relasi, modifier, animasi
    for od in D["objects"]:
        obj = lookup("objects", od["name"])
        for vgn in od["vertex_groups"]:
            obj.vertex_groups.new(name=vgn)
        if od["type"] == "MESH":
            me, A = mesh_cache[od["data"]]
            if od["data"] not in weighted_meshes:
                weighted_meshes.add(od["data"])
                apply_vertex_groups(obj, A)
                apply_shape_keys(obj, D["meshes"][od["data"]].get("shape_keys"), A)
        for i, sd in enumerate(od["material_slots"]):
            if i >= len(obj.material_slots):
                break
            slot = obj.material_slots[i]
            try:
                slot.link = sd["link"]
            except Exception:
                pass
            if sd["link"] == "OBJECT" or slot.material is None:
                slot.material = lookup("materials", sd["material"])
        if od["parent"]:
            obj.parent = lookup("objects", od["parent"])
            try:
                obj.parent_type = od["parent_type"]
            except Exception:
                pass
            if od["parent_type"] == "BONE":
                obj.parent_bone = od["parent_bone"]
            obj.matrix_parent_inverse = Matrix(od["matrix_parent_inverse"])
        obj.rotation_mode = od["rotation_mode"]
        for k in ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle",
                  "scale", "hide_viewport", "hide_render", "display_type", "show_in_front",
                  "color", "pass_index", "empty_display_type", "empty_display_size"):
            try:
                setattr(obj, k, od[k])
            except Exception:
                pass
        if od.get("instance_collection"):
            obj.instance_type = "COLLECTION"
            obj.instance_collection = lookup("collections", od["instance_collection"])
        set_custom_props(obj, od.get("custom_props"))

    bpy.context.view_layer.update()  # supaya obj.pose terbentuk untuk armature baru
    for od in D["objects"]:
        obj = lookup("objects", od["name"])
        add_modifiers(obj, od["modifiers"])
        add_constraints(obj, od["constraints"], od["name"])
        if od["type"] == "ARMATURE":
            for pd in od.get("pose", []):
                pb = obj.pose.bones.get(pd["name"])
                if pb is None:
                    continue
                pb.rotation_mode = pd["rotation_mode"]
                for k in ("location", "rotation_euler", "rotation_quaternion",
                          "rotation_axis_angle", "scale", "lock_location", "lock_rotation",
                          "lock_scale"):
                    try:
                        setattr(pb, k, pd[k])
                    except Exception:
                        pass
                if pd["custom_shape"]:
                    pb.custom_shape = lookup("objects", pd["custom_shape"])
                add_constraints(pb, pd["constraints"], od["name"] + ":" + pd["name"])
                set_custom_props(pb, pd.get("custom_props"))
        apply_anim(obj, od.get("animation"))
        if od.get("data_animation") and obj.data is not None:
            apply_anim(obj.data, od["data_animation"])

    # kamera & lampu (setelah obyek ada, untuk dof.focus_object)
    for cd in D["cameras"]:
        rna_load(lookup("cameras", cd["name"]), cd["props"])
    for ld in D["lights"]:
        rna_load(lookup("lights", ld["name"]), ld["props"], skip={"type", "use_nodes"})

    # view layer state
    def apply_layer(lc):
        st = None
        for cd in D["collections"]:
            if lookup("collections", cd["name"]) == lc.collection:
                st = cd.get("layer")
                break
        if st:
            for k in ("exclude", "holdout", "indirect_only"):
                try:
                    setattr(lc, k, st[k])
                except Exception:
                    pass
            try:
                lc.hide_viewport = st["hide"]
            except Exception:
                pass
        for ch in lc.children:
            apply_layer(ch)
    apply_layer(bpy.context.view_layer.layer_collection)
    vl = bpy.context.view_layer
    for od in D["objects"]:
        obj = lookup("objects", od["name"])
        if od.get("hide") and obj.name in vl.objects:
            obj.hide_set(True)

    # scene
    sd = D["scene"]
    scene.name = sd["name"]
    r = scene.render
    for k in ("frame_start", "frame_end", "frame_current"):
        setattr(scene, k, sd[k])
    for k in ("fps", "fps_base", "resolution_x", "resolution_y", "resolution_percentage",
              "film_transparent"):
        try:
            setattr(r, k, sd[k])
        except Exception:
            pass
    eng = sd["engine"]
    for e in [eng, {"BLENDER_EEVEE_NEXT": "BLENDER_EEVEE",
                    "BLENDER_EEVEE": "BLENDER_EEVEE_NEXT"}.get(eng), "BLENDER_EEVEE"]:
        if not e:
            continue
        try:
            r.engine = e
            break
        except Exception:
            continue
    vs = scene.view_settings
    for vt in (sd["view_transform"], "Filmic", "Standard"):
        try:
            vs.view_transform = vt
            break
        except Exception:
            continue
    if vs.view_transform != sd["view_transform"]:
        warn(f"View transform '{sd['view_transform']}' tidak ada, pakai '{vs.view_transform}'")
    try:
        vs.look = sd["look"]
    except Exception:
        vs.look = "None"
    vs.exposure = sd["exposure"]
    vs.gamma = sd["gamma"]
    try:
        scene.unit_settings.system = sd["unit_system"]
        scene.unit_settings.scale_length = sd["scale_length"]
    except Exception:
        pass
    if sd.get("cycles") and hasattr(scene, "cycles"):
        for k, v in sd["cycles"].items():
            try:
                setattr(scene.cycles, k, v)
            except Exception:
                pass
    scene.camera = lookup("objects", sd["camera"])
    scene.world = lookup("worlds", sd["world"])
    act = lookup("objects", D.get("active_object"))
    if act is not None and act.name in vl.objects:
        vl.objects.active = act

    src_warn = D.get("warnings", [])
    print(f"[legacy-import] Selesai. {_skipped['count']} properti kecil dilewati "
          f"(tidak ada di versi ini).")
    if src_warn:
        print("[legacy-import] Peringatan dari tahap export:")
        for w in src_warn:
            print("   -", w)

    if out_path:
        if pack:
            try:
                bpy.ops.file.pack_all()
            except Exception as e:
                warn(f"Gagal pack tekstur: {e}")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_path))
        print(f"[legacy-import] Tersimpan: {out_path}")
    return _warnings


# ---------------------------------------------------------------- add-on UI

class IMPORT_OT_legacy_package(bpy.types.Operator):
    """Bangun ulang scene dari Legacy Package (folder berisi scene.json, atau .zip)"""
    bl_idname = "import_scene.legacy_package"
    bl_label = "Import Legacy Package"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.zip;*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        p = self.filepath
        if p.lower().endswith(".json"):
            p = os.path.dirname(p)
        w = import_package(p, out_path=None, pack=False, clear_scene=True)
        self.report({"INFO"}, f"Import selesai ({len(w)} peringatan, lihat System Console). "
                              f"Simpan dengan File > Save As.")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(IMPORT_OT_legacy_package.bl_idname,
                         text="Legacy Package (scene.json / .zip)")


def register():
    bpy.utils.register_class(IMPORT_OT_legacy_package)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    bpy.utils.unregister_class(IMPORT_OT_legacy_package)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) >= 2:
        VERBOSE = "--verbose" in argv
        import_package(os.path.abspath(argv[0]), argv[1], pack="--no-pack" not in argv)
    else:
        register()
