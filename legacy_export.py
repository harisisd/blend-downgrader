"""
Legacy Package EXPORTER  —  jalankan di Blender BARU (4.x / 5.x)

Membongkar scene aktif menjadi "legacy package" (folder berisi scene.json,
array mesh .npz, dan tekstur) yang bisa dibangun ulang di Blender 3.x
dengan legacy_import.py.

Cara pakai (command line):
    Blender -b input.blend --python legacy_export.py -- OUTPUT_FOLDER [--keep-modifiers] [--zip]

Atau install sebagai add-on:  File > Export > Legacy Package (Blender 3.x)
"""

bl_info = {
    "name": "Legacy Package Export (buka di Blender 3.x)",
    "author": "blend_downgrade",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "File > Export > Legacy Package (Blender 3.x)",
    "description": "Export scene ke paket netral yang bisa dibangun ulang di Blender 3.x",
    "category": "Import-Export",
}

import bpy
import os
import sys
import json
import shutil
import re
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion, Color

FORMAT_VERSION = 1
_warnings = []


def warn(msg):
    if msg not in _warnings:
        _warnings.append(msg)
        print("[legacy-export] PERINGATAN:", msg)


# ---------------------------------------------------------------- generic RNA

ID_COLLECTIONS = [
    (bpy.types.Object, "objects"), (bpy.types.Mesh, "meshes"),
    (bpy.types.Material, "materials"), (bpy.types.Image, "images"),
    (bpy.types.NodeTree, "node_groups"), (bpy.types.Collection, "collections"),
    (bpy.types.Armature, "armatures"), (bpy.types.Camera, "cameras"),
    (bpy.types.Light, "lights"), (bpy.types.World, "worlds"),
    (bpy.types.Text, "texts"), (bpy.types.Action, "actions"),
    (bpy.types.Scene, "scenes"), (bpy.types.Texture, "textures"),
]

BASE_SKIP = {
    "rna_type", "name", "type", "bl_idname", "bl_label", "bl_description",
    "bl_icon", "bl_static_type", "is_override_data", "persistent_uid",
    "is_active", "show_expanded", "show_expanded_ui", "is_valid",
    "error_location", "error_rotation", "execution_time", "is_bound",
    "is_override_data_editable",
}


def id_ref(idb):
    for cls, attr in ID_COLLECTIONS:
        if isinstance(idb, cls):
            return {"__id__": attr, "name": idb.name}
    return None


def jv(v):
    """Konversi nilai Blender -> nilai JSON."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return id_ref(v)
    if isinstance(v, Matrix):
        return [list(r) for r in v]
    if isinstance(v, (Vector, Euler, Quaternion, Color)):
        return list(v)
    if isinstance(v, (set, frozenset)):
        return {"__set__": sorted(v)}
    if isinstance(v, bpy.types.bpy_struct):
        return None
    try:
        return [jv(x) for x in v]
    except TypeError:
        return None


def rna_dump(struct, skip=(), recurse=()):
    """Dump semua properti sederhana (yang bisa ditulis) dari sebuah struct RNA.
    Properti ENUM ditaruh duluan karena sering menentukan properti lain."""
    enums, rest = {}, {}
    for p in struct.bl_rna.properties:
        pid = p.identifier
        if pid in BASE_SKIP or pid in skip or p.type == "COLLECTION":
            continue
        try:
            val = getattr(struct, pid)
        except Exception:
            continue
        if p.type == "POINTER":
            if pid in recurse and val is not None:
                rest[pid] = {"__struct__": rna_dump(val)}
            elif not p.is_readonly:
                if val is None:
                    rest[pid] = None
                elif isinstance(val, bpy.types.ID):
                    r = id_ref(val)
                    if r:
                        rest[pid] = r
            continue
        if p.is_readonly:
            continue
        v = jv(val)
        if v is None:
            continue
        (enums if p.type == "ENUM" else rest)[pid] = v
    enums.update(rest)
    return enums


def custom_props(idb):
    out = {}
    try:
        for k in idb.keys():
            if k.startswith("_") or k in ("cycles",):
                continue
            v = idb[k]
            if isinstance(v, (bool, int, float, str)):
                out[k] = v
            else:
                try:
                    out[k] = [float(x) for x in v]
                except Exception:
                    pass
    except Exception:
        pass
    return out


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:80] or "unnamed"


# ---------------------------------------------------------------- node trees

NODE_BASE = {p.identifier for p in bpy.types.Node.bl_rna.properties} | {
    "color_ramp", "mapping", "image_user", "texture_mapping", "color_mapping",
    "interface", "location_absolute",
}


def sock_key(sock, sockets):
    idx, occ = 0, 0
    for i, s in enumerate(sockets):
        if s == sock:
            idx = i
            break
        if s.name == sock.name:
            occ += 1
    return {"identifier": sock.identifier, "name": sock.name, "index": idx, "occ": occ}


def dump_sockets(sockets):
    out = []
    for s in sockets:
        d = sock_key(s, sockets)
        d["hide"] = s.hide
        if hasattr(s, "default_value"):
            try:
                v = jv(s.default_value)
                if v is not None:
                    d["value"] = v
            except Exception:
                pass
        out.append(d)
    return out


def dump_ramp(r):
    return {
        "color_mode": r.color_mode, "interpolation": r.interpolation,
        "hue_interpolation": r.hue_interpolation,
        "elements": [{"position": e.position, "color": list(e.color)} for e in r.elements],
    }


def dump_curvemap(m):
    d = {"use_clip": m.use_clip, "clip_min_x": m.clip_min_x, "clip_min_y": m.clip_min_y,
         "clip_max_x": m.clip_max_x, "clip_max_y": m.clip_max_y,
         "black_level": list(m.black_level), "white_level": list(m.white_level),
         "curves": []}
    for c in m.curves:
        d["curves"].append([{"location": list(p.location), "handle_type": p.handle_type}
                            for p in c.points])
    return d


def dump_tree(tree):
    nodes = []
    for n in tree.nodes:
        nd = {
            "name": n.name, "bl_idname": n.bl_idname, "label": n.label,
            "location": list(n.location), "width": n.width, "hide": n.hide,
            "mute": n.mute, "parent": n.parent.name if n.parent else None,
            "props": rna_dump(n, skip=NODE_BASE),
            "inputs": dump_sockets(n.inputs), "outputs": dump_sockets(n.outputs),
        }
        if n.use_custom_color:
            nd["color"] = list(n.color)
        if getattr(n, "color_ramp", None) is not None:
            nd["color_ramp"] = dump_ramp(n.color_ramp)
        m = getattr(n, "mapping", None)
        if isinstance(m, bpy.types.CurveMapping):
            nd["mapping"] = dump_curvemap(m)
        nodes.append(nd)
    links = []
    for l in tree.links:
        if getattr(l, "is_hidden", False):
            continue
        links.append({
            "from_node": l.from_node.name,
            "from_socket": sock_key(l.from_socket, l.from_node.outputs),
            "to_node": l.to_node.name,
            "to_socket": sock_key(l.to_socket, l.to_node.inputs),
            "muted": getattr(l, "is_muted", False),
        })
    return {"nodes": nodes, "links": links}


def dump_interface(tree):
    items = []
    if hasattr(tree, "interface"):  # 4.0+
        for it in tree.interface.items_tree:
            if it.item_type != "SOCKET":
                continue
            d = {"in_out": it.in_out, "socket_type": it.socket_type,
                 "name": it.name, "description": it.description,
                 "subtype": getattr(it, "subtype", "NONE")}
            for k in ("default_value", "min_value", "max_value", "hide_value"):
                if hasattr(it, k):
                    try:
                        v = jv(getattr(it, k))
                        if v is not None:
                            d[k] = v
                    except Exception:
                        pass
            items.append(d)
    else:  # 3.x (untuk berjaga-jaga)
        for io, coll in (("INPUT", tree.inputs), ("OUTPUT", tree.outputs)):
            for s in coll:
                d = {"in_out": io, "socket_type": s.bl_socket_idname, "name": s.name,
                     "subtype": "NONE"}
                for k in ("default_value", "min_value", "max_value"):
                    if hasattr(s, k):
                        d[k] = jv(getattr(s, k))
                items.append(d)
    return items


# ---------------------------------------------------------------- images

FMT_EXT = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr", "OPEN_EXR_MULTILAYER": ".exr",
           "TARGA": ".tga", "TARGA_RAW": ".tga", "TIFF": ".tif", "HDR": ".hdr",
           "WEBP": ".webp", "BMP": ".bmp", "JPEG2000": ".jp2"}


def export_images(outdir):
    texdir = os.path.join(outdir, "textures")
    os.makedirs(texdir, exist_ok=True)
    out = []
    used_files = set()
    for img in bpy.data.images:
        if img.type not in ("IMAGE", "UV_TEST") or img.users == 0:
            continue
        d = {"name": img.name, "colorspace": img.colorspace_settings.name,
             "alpha_mode": img.alpha_mode, "file": None}
        resave = img.packed_file is None and (img.source == "GENERATED" or img.is_dirty)
        src_ext = os.path.splitext(img.filepath)[1].lower()
        ext = ".png" if resave else (src_ext or FMT_EXT.get(img.file_format, ".png"))
        base = safe_name(os.path.splitext(img.name)[0])
        fname = base + ext
        i = 1
        while fname in used_files:
            fname = f"{base}_{i}{ext}"
            i += 1
        dst = os.path.join(texdir, fname)
        try:
            if img.source == "TILED":
                warn(f"Gambar UDIM '{img.name}' tidak didukung, dilewati")
                continue
            if img.source in ("SEQUENCE", "MOVIE"):
                warn(f"Gambar '{img.name}' berupa sequence/movie, hanya path yang dicatat")
            if img.packed_file is not None:
                with open(dst, "wb") as f:
                    f.write(img.packed_file.data)
            elif resave:
                try:
                    img.save(filepath=dst)
                except Exception:
                    img.save_render(dst)
            else:
                src = bpy.path.abspath(img.filepath, library=img.library)
                if not os.path.isfile(src):
                    warn(f"File tekstur hilang: {src} (gambar '{img.name}')")
                    continue
                shutil.copy2(src, dst)
            d["file"] = "textures/" + fname
            used_files.add(fname)
        except Exception as e:
            warn(f"Gagal export gambar '{img.name}': {e}")
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------- meshes

def _get(coll, attr, n, dtype, width=1):
    a = np.empty(n * width, dtype=dtype)
    coll.foreach_get(attr, a)
    return a


def _attr(me, name, domain_len, dtype=np.float32, prop="value"):
    a = me.attributes.get(name)
    if a is None:
        return None
    out = np.empty(domain_len, dtype=dtype)
    try:
        a.data.foreach_get(prop, out)
        return out
    except Exception:
        return None


def dump_mesh(me, key, outdir, with_shape_keys):
    nv, ne, nl, npoly = len(me.vertices), len(me.edges), len(me.loops), len(me.polygons)
    A = {}
    A["co"] = _get(me.vertices, "co", nv, np.float32, 3)
    A["edges"] = _get(me.edges, "vertices", ne, np.int32, 2)
    A["loop_vert"] = _get(me.loops, "vertex_index", nl, np.int32)
    A["loop_edge"] = _get(me.loops, "edge_index", nl, np.int32)
    A["poly_start"] = _get(me.polygons, "loop_start", npoly, np.int32)
    A["poly_total"] = _get(me.polygons, "loop_total", npoly, np.int32)
    A["poly_mat"] = _get(me.polygons, "material_index", npoly, np.int32)
    try:
        A["poly_smooth"] = _get(me.polygons, "use_smooth", npoly, bool)
    except Exception:
        sf = _attr(me, "sharp_face", npoly, bool)
        A["poly_smooth"] = ~sf if sf is not None else np.ones(npoly, bool)

    sharp = _attr(me, "sharp_edge", ne, bool)
    if sharp is None:
        try:
            sharp = _get(me.edges, "use_edge_sharp", ne, bool)
        except Exception:
            sharp = None
    if sharp is not None and sharp.any():
        A["edge_sharp"] = sharp
    seam = None
    try:
        seam = _get(me.edges, "use_seam", ne, bool)
    except Exception:
        for nm in ("uv_seam", ".uv_seam"):
            seam = _attr(me, nm, ne, bool)
            if seam is not None:
                break
    if seam is not None and seam.any():
        A["edge_seam"] = seam
    for nm, key2, n in (("crease_edge", "edge_crease", ne),
                        ("bevel_weight_edge", "edge_bevel", ne),
                        ("crease_vert", "vert_crease", nv),
                        ("bevel_weight_vert", "vert_bevel", nv)):
        a = _attr(me, nm, n)
        if a is not None and a.any():
            A[key2] = a

    meta = {"name": me.name, "uv_layers": [], "color_attributes": [],
            "materials": [m.name if m else None for m in me.materials],
            "shape_keys": None, "custom_normals": False}

    for i, uv in enumerate(me.uv_layers):
        A[f"uv_{i}"] = _get(uv.data, "uv", nl, np.float32, 2)
        meta["uv_layers"].append({"name": uv.name, "active": uv == me.uv_layers.active,
                                  "active_render": uv.active_render})

    for i, ca in enumerate(getattr(me, "color_attributes", [])):
        n = nv if ca.domain == "POINT" else nl
        try:
            A[f"col_{i}"] = _get(ca.data, "color", n, np.float32, 4)
        except Exception:
            continue
        meta["color_attributes"].append({
            "name": ca.name, "domain": ca.domain, "data_type": ca.data_type,
            "active": ca.name == getattr(me.color_attributes.active_color, "name", None),
            "render": ca.name == me.color_attributes.default_color_name
            if hasattr(me.color_attributes, "default_color_name") else False})

    if me.has_custom_normals:
        try:
            if hasattr(me, "corner_normals"):  # 4.1+
                A["custom_normals"] = _get(me.corner_normals, "vector", nl, np.float32, 3)
            else:
                me.calc_normals_split()
                A["custom_normals"] = _get(me.loops, "normal", nl, np.float32, 3)
            meta["custom_normals"] = True
        except Exception as e:
            warn(f"Custom normals mesh '{me.name}' gagal dibaca: {e}")

    # vertex groups (bobot disimpan di mesh)
    vg_v, vg_g, vg_w = [], [], []
    for v in me.vertices:
        for g in v.groups:
            vg_v.append(v.index)
            vg_g.append(g.group)
            vg_w.append(g.weight)
    if vg_v:
        A["vg_vert"] = np.array(vg_v, np.int32)
        A["vg_group"] = np.array(vg_g, np.int32)
        A["vg_weight"] = np.array(vg_w, np.float32)

    if with_shape_keys and me.shape_keys:
        sk = me.shape_keys
        meta["shape_keys"] = {"use_relative": sk.use_relative, "name": sk.name, "blocks": [],
                              "animation": dump_anim(sk)}
        for i, kb in enumerate(sk.key_blocks):
            A[f"sk_{i}"] = _get(kb.data, "co", nv, np.float32, 3)
            meta["shape_keys"]["blocks"].append({
                "name": kb.name, "value": kb.value, "slider_min": kb.slider_min,
                "slider_max": kb.slider_max, "mute": kb.mute,
                "relative_key": kb.relative_key.name, "vertex_group": kb.vertex_group,
                "interpolation": kb.interpolation})

    fn = f"meshes/{safe_name(key)}.npz"
    os.makedirs(os.path.join(outdir, "meshes"), exist_ok=True)
    np.savez_compressed(os.path.join(outdir, fn), **A)
    meta["file"] = fn
    return meta


# ---------------------------------------------------------------- animation

def _fcurves_of(anim):
    act = anim.action
    if hasattr(act, "layers") and len(act.layers) > 0:
        slot = getattr(anim, "action_slot", None)
        fcs = []
        for layer in act.layers:
            for strip in layer.strips:
                try:
                    cb = strip.channelbag(slot) if slot else None
                except Exception:
                    cb = None
                if cb:
                    fcs.extend(cb.fcurves)
        return fcs
    try:
        return list(act.fcurves)
    except Exception:
        return []


def dump_anim(idb):
    anim = getattr(idb, "animation_data", None)
    if anim is None:
        return None
    if len(anim.nla_tracks):
        warn(f"NLA track pada '{idb.name}' tidak ikut di-export (hanya action aktif)")
    if anim.action is None:
        return None
    curves = []
    for fc in _fcurves_of(anim):
        n = len(fc.keyframe_points)
        kp = fc.keyframe_points
        c = {"data_path": fc.data_path, "index": fc.array_index,
             "group": fc.group.name if fc.group else None,
             "extrapolation": fc.extrapolation,
             "co": _get(kp, "co", n, np.float32, 2).reshape(-1, 2).tolist(),
             "hl": _get(kp, "handle_left", n, np.float32, 2).reshape(-1, 2).tolist(),
             "hr": _get(kp, "handle_right", n, np.float32, 2).reshape(-1, 2).tolist(),
             "interp": [k.interpolation for k in kp],
             "easing": [k.easing for k in kp],
             "hlt": [k.handle_left_type for k in kp],
             "hrt": [k.handle_right_type for k in kp],
             "modifiers": [m.type for m in fc.modifiers if m.type == "CYCLES"]}
        if len(fc.modifiers) > len(c["modifiers"]):
            warn(f"Modifier F-curve selain Cycles pada '{idb.name}' dilewati")
        curves.append(c)
    return {"action": anim.action.name, "fcurves": curves}


# ---------------------------------------------------------------- objects

CURVE_LIKE = {"CURVE", "SURFACE", "FONT", "META"}
MOD_SKIP = {"name", "type", "show_expanded", "is_override_data", "persistent_uid",
            "use_apply_on_spline", "is_active", "use_pin_to_last"}


def dump_constraints(cons):
    return [{"name": c.name, "type": c.type, "props": rna_dump(c, skip=MOD_SKIP)}
            for c in cons]


def dump_modifiers(obj, only_types=None):
    out = []
    for m in obj.modifiers:
        if only_types is not None and m.type not in only_types:
            continue
        if m.type == "NODES":
            warn(f"Geometry Nodes di '{obj.name}' tidak bisa dibawa sebagai modifier "
                 f"(obyek punya shape key / --keep-modifiers / tidak bisa dievaluasi). Hasilnya hilang.")
            continue
        out.append({"name": m.name, "type": m.type, "props": rna_dump(m, skip=MOD_SKIP)})
    return out


def collect_objects(scene):
    objs = list(scene.objects)
    seen = set(objs)
    for o in list(objs):
        ic = o.instance_collection if o.instance_type == "COLLECTION" else None
        if ic:
            for x in ic.all_objects:
                if x not in seen:
                    seen.add(x)
                    objs.append(x)
    return objs


def export_scene(outdir, keep_modifiers=False):
    _warnings.clear()
    os.makedirs(outdir, exist_ok=True)
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    data = {"format": FORMAT_VERSION,
            "source_version": bpy.app.version_string,
            "source_file": bpy.data.filepath,
            "images": export_images(outdir),
            "node_groups": [], "materials": [], "worlds": [], "meshes": {},
            "armatures": [], "cameras": [], "lights": [], "collections": [],
            "objects": [], "scene": {}, "warnings": _warnings}

    for ng in bpy.data.node_groups:
        if ng.bl_idname == "ShaderNodeTree":
            data["node_groups"].append({"name": ng.name, "interface": dump_interface(ng),
                                        "tree": dump_tree(ng)})
        elif ng.bl_idname == "CompositorNodeTree":
            warn(f"Compositor node group '{ng.name}' tidak di-export")

    objects = collect_objects(scene)
    used_mats = set()
    for o in objects:
        for s in o.material_slots:
            if s.material:
                used_mats.add(s.material)
        if o.type == "MESH":
            used_mats.update(m for m in o.data.materials if m)
    for mat in bpy.data.materials:
        if mat not in used_mats and not mat.use_fake_user:
            continue
        md = {"name": mat.name, "use_nodes": bool(mat.node_tree),
              "props": rna_dump(mat, skip={"node_tree", "grease_pencil", "line_color",
                                           "use_preview_world", "preview_render_type"}),
              "tree": dump_tree(mat.node_tree) if mat.node_tree else None,
              "custom_props": custom_props(mat)}
        if mat.grease_pencil:
            warn(f"Material grease pencil '{mat.name}' dilewati")
            continue
        data["materials"].append(md)

    for w in bpy.data.worlds:
        if w.users == 0:
            continue
        data["worlds"].append({"name": w.name, "color": list(w.color),
                               "tree": dump_tree(w.node_tree) if w.node_tree else None})

    # ---- objek
    obj_names = {o.name for o in objects}
    in_layer = set(view_layer.objects)
    # Collection yang di-exclude / obyek yang disable di viewport tidak dievaluasi
    # depsgraph, jadi modifier-nya (termasuk Geometry Nodes) tidak bisa di-apply.
    # Aktifkan sementara, lalu kembalikan setelah semua obyek di-export.
    restore = []

    def _walk_lc(lc):
        yield lc
        for ch in lc.children:
            yield from _walk_lc(ch)

    for lc in _walk_lc(view_layer.layer_collection):
        if lc.exclude:
            restore.append((lc, "exclude", True))
            lc.exclude = False
    for c in bpy.data.collections:
        if c.hide_viewport:
            restore.append((c, "hide_viewport", True))
            c.hide_viewport = False
    orig_hide_vp = {}
    for o in objects:
        orig_hide_vp[o] = o.hide_viewport
        if o.hide_viewport and o.type in {"MESH"} | CURVE_LIKE:
            restore.append((o, "hide_viewport", True))
            o.hide_viewport = False
    if restore:
        view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluable = set(view_layer.objects)

    arm_done, cam_done, light_done = set(), set(), set()
    for o in objects:
        od = {"name": o.name, "type": o.type, "data": None,
              "parent": o.parent.name if o.parent and o.parent.name in obj_names else None,
              "parent_type": o.parent_type, "parent_bone": o.parent_bone,
              "matrix_parent_inverse": jv(o.matrix_parent_inverse),
              "location": list(o.location), "rotation_mode": o.rotation_mode,
              "rotation_euler": list(o.rotation_euler),
              "rotation_quaternion": list(o.rotation_quaternion),
              "rotation_axis_angle": list(o.rotation_axis_angle), "scale": list(o.scale),
              "hide_viewport": orig_hide_vp.get(o, o.hide_viewport), "hide_render": o.hide_render,
              "hide": o.hide_get() if o in in_layer else False,
              "display_type": o.display_type, "show_in_front": o.show_in_front,
              "color": list(o.color), "pass_index": o.pass_index,
              "collections": ["__scene__" if c == scene.collection else c.name
                              for c in o.users_collection],
              "material_slots": [{"link": s.link,
                                  "material": s.material.name if s.material else None}
                                 for s in o.material_slots],
              "vertex_groups": [vg.name for vg in o.vertex_groups],
              "modifiers": [], "constraints": dump_constraints(o.constraints),
              "empty_display_type": o.empty_display_type,
              "empty_display_size": o.empty_display_size,
              "instance_type": o.instance_type,
              "instance_collection": o.instance_collection.name if o.instance_collection else None,
              "custom_props": custom_props(o), "animation": dump_anim(o),
              "data_animation": None}

        if o.type == "MESH" or o.type in CURVE_LIKE:
            mods = list(o.modifiers)
            has_sk = o.type == "MESH" and o.data.shape_keys is not None
            non_arm = [m for m in mods if m.type != "ARMATURE"]
            if o.type == "MESH" and (not non_arm or has_sk or keep_modifiers or o not in evaluable):
                key = "mesh:" + o.data.name
                if key not in data["meshes"]:
                    data["meshes"][key] = dump_mesh(o.data, key, outdir, True)
                od["modifiers"] = dump_modifiers(o)
                if has_sk and non_arm and not keep_modifiers:
                    print(f"[legacy-export] '{o.name}' punya shape key: modifier dibawa apa adanya")
            else:
                arm_mods = [m for m in mods if m.type == "ARMATURE"]
                saved = [(m, m.show_viewport) for m in arm_mods]
                for m, _ in saved:
                    m.show_viewport = False
                if saved:
                    view_layer.update()
                    depsgraph = bpy.context.evaluated_depsgraph_get()
                try:
                    if o in evaluable:
                        ev = o.evaluated_get(depsgraph)
                        me = bpy.data.meshes.new_from_object(
                            ev, preserve_all_data_layers=True, depsgraph=depsgraph)
                    else:
                        me = bpy.data.meshes.new_from_object(o)
                finally:
                    for m, v in saved:
                        m.show_viewport = v
                    if saved:
                        view_layer.update()
                        depsgraph = bpy.context.evaluated_depsgraph_get()
                if o.type in CURVE_LIKE:
                    warn(f"Obyek {o.type.lower()} '{o.name}' dikonversi jadi mesh")
                key = "obj:" + o.name
                md = dump_mesh(me, key, outdir, False)
                md["name"] = (o.data.name if o.type == "MESH" else o.name + "_mesh")
                md["materials"] = [m.name if m else None for m in o.data.materials] \
                    if hasattr(o.data, "materials") else md["materials"]
                data["meshes"][key] = md
                bpy.data.meshes.remove(me)
                od["modifiers"] = dump_modifiers(o, only_types={"ARMATURE"})
            od["type"] = "MESH"
            od["data"] = key
            if o.type == "MESH":
                od["data_animation"] = dump_anim(o.data)

        elif o.type == "ARMATURE":
            arm = o.data
            od["data"] = arm.name
            if arm.name not in arm_done:
                arm_done.add(arm.name)
                bones = []
                for b in arm.bones:
                    bones.append({
                        "name": b.name, "parent": b.parent.name if b.parent else None,
                        "head": list(b.head_local), "tail": list(b.tail_local),
                        "matrix": jv(b.matrix_local), "use_connect": b.use_connect,
                        "use_deform": b.use_deform,
                        "use_inherit_rotation": b.use_inherit_rotation,
                        "inherit_scale": b.inherit_scale,
                        "use_local_location": b.use_local_location,
                        "bbone_segments": b.bbone_segments,
                        "envelope_distance": b.envelope_distance,
                        "head_radius": b.head_radius, "tail_radius": b.tail_radius,
                        "hide": b.hide,
                        "collections": [c.name for c in getattr(b, "collections", [])]})
                data["armatures"].append({"name": arm.name, "display_type": arm.display_type,
                                          "show_names": arm.show_names,
                                          "show_axes": arm.show_axes, "bones": bones,
                                          "pose_position": arm.pose_position,
                                          "bone_collections": [
                                              {"name": c.name, "is_visible": c.is_visible,
                                               "parent": c.parent.name
                                               if getattr(c, "parent", None) else None}
                                              for c in getattr(arm, "collections_all", [])]})
            od["pose"] = [{
                "name": pb.name, "rotation_mode": pb.rotation_mode,
                "location": list(pb.location), "rotation_euler": list(pb.rotation_euler),
                "rotation_quaternion": list(pb.rotation_quaternion),
                "rotation_axis_angle": list(pb.rotation_axis_angle), "scale": list(pb.scale),
                "custom_shape": pb.custom_shape.name if pb.custom_shape else None,
                "lock_location": list(pb.lock_location), "lock_rotation": list(pb.lock_rotation),
                "lock_scale": list(pb.lock_scale),
                "constraints": dump_constraints(pb.constraints),
                "custom_props": custom_props(pb)} for pb in o.pose.bones]
            od["modifiers"] = dump_modifiers(o)

        elif o.type == "CAMERA":
            od["data"] = o.data.name
            if o.data.name not in cam_done:
                cam_done.add(o.data.name)
                data["cameras"].append({"name": o.data.name,
                                        "props": rna_dump(o.data, recurse=("dof",),
                                                          skip={"background_images"})})
            od["data_animation"] = dump_anim(o.data)

        elif o.type == "LIGHT":
            od["data"] = o.data.name
            if o.data.name not in light_done:
                light_done.add(o.data.name)
                data["lights"].append({"name": o.data.name, "type": o.data.type,
                                       "props": rna_dump(o.data, skip={"node_tree"}),
                                       "tree": dump_tree(o.data.node_tree)
                                       if o.data.use_nodes and o.data.node_tree else None})
            od["data_animation"] = dump_anim(o.data)

        elif o.type == "EMPTY":
            pass
        else:
            warn(f"Tipe obyek {o.type} ('{o.name}') tidak didukung, dijadikan Empty")
            od["type"] = "EMPTY"
        data["objects"].append(od)

    for target, attr, val in reversed(restore):
        setattr(target, attr, val)
    if restore:
        view_layer.update()

    # ---- collections
    def lc_map(lc, out):
        out[lc.collection.name] = {"exclude": lc.exclude, "hide": lc.hide_viewport,
                                   "holdout": lc.holdout, "indirect_only": lc.indirect_only}
        for c in lc.children:
            lc_map(c, out)
    layer_state = {}
    lc_map(view_layer.layer_collection, layer_state)

    wanted = set()

    def walk(c):
        for ch in c.children:
            wanted.add(ch)
            walk(ch)
    walk(scene.collection)
    for o in objects:
        if o.instance_collection:
            wanted.add(o.instance_collection)
            walk(o.instance_collection)
        wanted.update(c for c in o.users_collection if c != scene.collection)
    for c in bpy.data.collections:
        if c in wanted:
            data["collections"].append({
                "name": c.name, "children": [ch.name for ch in c.children],
                "hide_viewport": c.hide_viewport, "hide_render": c.hide_render,
                "hide_select": c.hide_select, "instance_offset": list(c.instance_offset),
                "layer": layer_state.get(c.name)})

    # ---- scene
    r = scene.render
    vs = scene.view_settings
    sd = {"name": scene.name, "frame_start": scene.frame_start, "frame_end": scene.frame_end,
          "frame_current": scene.frame_current, "fps": r.fps, "fps_base": r.fps_base,
          "resolution_x": r.resolution_x, "resolution_y": r.resolution_y,
          "resolution_percentage": r.resolution_percentage, "engine": r.engine,
          "film_transparent": r.film_transparent,
          "camera": scene.camera.name if scene.camera else None,
          "world": scene.world.name if scene.world else None,
          "children": [c.name for c in scene.collection.children],
          "view_transform": vs.view_transform, "look": vs.look,
          "exposure": vs.exposure, "gamma": vs.gamma,
          "unit_system": scene.unit_settings.system,
          "scale_length": scene.unit_settings.scale_length}
    if hasattr(scene, "cycles"):
        try:
            sd["cycles"] = {"samples": scene.cycles.samples,
                            "preview_samples": scene.cycles.preview_samples,
                            "use_denoising": scene.cycles.use_denoising,
                            "device": scene.cycles.device}
        except Exception:
            pass
    data["scene"] = sd
    data["active_object"] = view_layer.objects.active.name if view_layer.objects.active else None

    if len(bpy.data.scenes) > 1:
        warn(f"File punya {len(bpy.data.scenes)} scene; hanya scene aktif '{scene.name}' yang di-export")

    with open(os.path.join(outdir, "scene.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print(f"[legacy-export] Selesai: {len(data['objects'])} obyek, "
          f"{len(data['materials'])} material, {len(data['meshes'])} mesh -> {outdir}")
    print(f"[legacy-export] {len(_warnings)} peringatan")
    return data


def zip_package(outdir):
    base = outdir.rstrip("/\\")
    return shutil.make_archive(base, "zip", base)


# ---------------------------------------------------------------- add-on UI

class EXPORT_OT_legacy_package(bpy.types.Operator):
    """Export scene ke Legacy Package untuk dibuka di Blender 3.x"""
    bl_idname = "export_scene.legacy_package"
    bl_label = "Export Legacy Package"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    keep_modifiers: bpy.props.BoolProperty(
        name="Pertahankan modifier",
        description="Bawa modifier sebagai modifier (tidak di-apply). Geometry Nodes tetap hilang",
        default=False)
    make_zip: bpy.props.BoolProperty(name="Buat juga .zip", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        name = safe_name(os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "scene")
        out = os.path.join(self.directory, name + "_legacy")
        export_scene(out, self.keep_modifiers)
        msg = out
        if self.make_zip:
            msg = zip_package(out)
        self.report({"INFO"}, f"Legacy package: {msg} ({len(_warnings)} peringatan, lihat scene.json)")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(EXPORT_OT_legacy_package.bl_idname, text="Legacy Package (Blender 3.x)")


def register():
    bpy.utils.register_class(EXPORT_OT_legacy_package)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func)
    bpy.utils.unregister_class(EXPORT_OT_legacy_package)


# ---------------------------------------------------------------- CLI

if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if argv:
        out = os.path.abspath(argv[0])
        export_scene(out, keep_modifiers="--keep-modifiers" in argv)
        if "--zip" in argv:
            print("[legacy-export] Zip:", zip_package(out))
    else:
        register()
