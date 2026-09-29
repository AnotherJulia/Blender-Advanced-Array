"""Advanced Array: Blender's new Array plus the legacy Array's extras.

The modifier is a copy of Blender's bundled "Array" node group (shapes, count
methods, offset methods, object transform, randomize, merge) with extra stages
hooked in after its output:

  * per-copy UV offset (plus random UV offset)
  * start / end caps
  * "Add Constant Offset" so relative + constant offsets can be combined
  * merge by distance (runs after caps/UVs so it also welds the caps)

It stays a normal, editable Geometry Nodes modifier.
"""

import os

import bpy

GROUP_NAME = "Advanced Array"
INDEX_ATTR = "_aa_copy_index"
ESSENTIALS_ARRAY = "Array"
# Bump when the node group's interface or wiring changes. Groups live inside
# the .blend, so older files keep their old group until it is replaced.
GROUP_REVISION = 2
REVISION_KEY = "advanced_array_revision"
UV_SLOTS = 4  # UV maps that can be offset (names are filled in automatically)


# --------------------------------------------------------------------------
# Node-building helpers
# --------------------------------------------------------------------------

def _in(node, name):
    """First *enabled* input socket called `name` (typed sockets share names)."""
    for s in node.inputs:
        if s.name == name and s.enabled:
            return s
    for s in node.inputs:
        if s.name == name:
            return s
    raise KeyError(f"{node.bl_idname} has no input '{name}'")


def _out(node, name):
    for s in node.outputs:
        if s.name == name and s.enabled:
            return s
    for s in node.outputs:
        if s.name == name:
            return s
    raise KeyError(f"{node.bl_idname} has no output '{name}'")


class _Builder:
    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links

    def node(self, idname, x, y, **props):
        n = self.nodes.new(idname)
        n.location = (x, y)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def link(self, out_socket, in_socket):
        self.links.new(out_socket, in_socket)

    def vmath(self, op, x, y, a=None, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", x, y, operation=op)
        if a is not None:
            self.link(a, n.inputs[0])
        if b is not None:
            self.link(b, n.inputs[1])
        if scale is not None:
            if isinstance(scale, (int, float)):
                _in(n, "Scale").default_value = scale
            else:
                self.link(scale, _in(n, "Scale"))
        return n

    def add_int(self, socket, k, x, y):
        n = self.node("ShaderNodeMath", x, y, operation="ADD")
        self.link(socket, n.inputs[0])
        n.inputs[1].default_value = k
        return n.outputs[0]

    def rand(self, dtype, x, y, seed, vmin=None, vmax=None, id_socket=None):
        n = self.node("FunctionNodeRandomValue", x, y, data_type=dtype)
        if vmin is not None:
            self.link(vmin, _in(n, "Min"))
        if vmax is not None:
            self.link(vmax, _in(n, "Max"))
        if id_socket is not None:
            self.link(id_socket, _in(n, "ID"))
        self.link(seed, _in(n, "Seed"))
        return n


def _new_socket(iface, name, socket_type, in_out="INPUT", default=None,
                minv=None, maxv=None, subtype=None, desc="", parent=None):
    kwargs = dict(name=name, in_out=in_out, socket_type=socket_type)
    if parent is not None:
        kwargs["parent"] = parent
    s = iface.new_socket(**kwargs)
    if subtype is not None:
        s.subtype = subtype
    if default is not None:
        s.default_value = default
    if minv is not None:
        s.min_value = minv
    if maxv is not None:
        s.max_value = maxv
    if desc:
        s.description = desc
    return s


# --------------------------------------------------------------------------
# Node group
# --------------------------------------------------------------------------

def _essentials_path():
    return os.path.join(bpy.utils.system_resource("DATAFILES"),
                        "assets", "nodes", "geometry_nodes_essentials.blend")


def _load_builtin_array():
    """Append a private copy of Blender's bundled 'Array' node group."""
    path = _essentials_path()
    if not os.path.exists(path):
        raise RuntimeError(
            "Blender's bundled Array node group was not found "
            f"({path}). Advanced Array needs a Blender version that ships it.")
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        if ESSENTIALS_ARRAY not in src.node_groups:
            raise RuntimeError("This Blender has no bundled 'Array' node group.")
        dst.node_groups = [ESSENTIALS_ARRAY]
    ng = dst.node_groups[0]
    try:
        ng.asset_clear()
    except Exception:
        pass
    return ng


def build_node_group():
    tree = _load_builtin_array()
    tree.name = GROUP_NAME
    tree[REVISION_KEY] = GROUP_REVISION
    iface = tree.interface
    nodes, links = tree.nodes, tree.links

    gi = next(n for n in nodes if n.bl_idname == "NodeGroupInput")
    go = next(n for n in nodes if n.bl_idname == "NodeGroupOutput")

    # 1. Force the built-in array to output instances: we realize ourselves,
    #    because per-copy UV offsets need each copy's index.
    for l in list(_out(gi, "Realize Instances").links):
        target = l.to_socket
        links.remove(l)
        target.default_value = False
    for it in list(iface.items_tree):
        if it.item_type == "SOCKET" and it.in_out == "INPUT" \
                and it.name == "Realize Instances":
            iface.remove(it)

    final_link = go.inputs[0].links[0]
    src = final_link.from_socket
    links.remove(final_link)

    # 2. New interface sockets
    p = iface.new_panel("Legacy Offset")
    _new_socket(iface, "Add Constant Offset", "NodeSocketBool", default=False,
                parent=p,
                desc="Also add a constant per-copy offset on top of the array's "
                     "own offset (legacy Relative + Constant combination). "
                     "Meant for Line arrays.")
    _new_socket(iface, "Extra Constant Offset", "NodeSocketVector",
                default=(0, 0, 0), subtype="TRANSLATION", parent=p)

    p = iface.new_panel("Caps")
    _new_socket(iface, "Start Cap", "NodeSocketObject", parent=p,
                desc="Object placed as an extra copy before the first copy")
    _new_socket(iface, "End Cap", "NodeSocketObject", parent=p,
                desc="Object placed as an extra copy after the last copy")

    p = iface.new_panel("UV")
    _new_socket(iface, "UV Offset Enabled", "NodeSocketBool", default=True,
                parent=p)
    # Blender's legacy array shifts every UV map. Nodes can't enumerate UV
    # maps, so there are hidden name slots that the add-on fills in from the
    # mesh's UV layers (see sync_uv_maps).
    for i in range(1, UV_SLOTS + 1):
        s = _new_socket(iface, f"UV Map {i}", "NodeSocketString",
                        default="UVMap" if i == 1 else "", parent=p)
        s.hide_in_modifier = True
    _new_socket(iface, "UV Offset", "NodeSocketVector", default=(0, 0, 0),
                subtype="XYZ", parent=p,
                desc="UV shift added per copy (X = U, Y = V; Z is ignored)")
    _new_socket(iface, "Random UV Offset", "NodeSocketVector",
                default=(0, 0, 0), subtype="XYZ", parent=p,
                desc="Each copy's UVs are additionally shifted randomly by up "
                     "to +/- this (X = U, Y = V)")

    G = {}
    for s in gi.outputs:
        G.setdefault(s.name, s)  # first one wins (some names repeat)

    # 3. Post-processing stage
    b = _Builder(tree)
    x0 = max(n.location.x for n in nodes) + 400

    # extra constant offset, per copy
    idx = b.node("GeometryNodeInputIndex", x0, -400)
    step = b.vmath("SCALE", x0 + 200, -400, G["Extra Constant Offset"],
                   scale=_out(idx, "Index"))
    sw_off = b.node("GeometryNodeSwitch", x0 + 400, -400, input_type="VECTOR")
    b.link(G["Add Constant Offset"], _in(sw_off, "Switch"))
    b.link(step.outputs[0], _in(sw_off, "True"))
    tr = b.node("GeometryNodeTranslateInstances", x0 + 600, 0)
    _in(tr, "Local Space").default_value = False
    b.link(src, _in(tr, "Instances"))
    b.link(_out(sw_off, "Output"), _in(tr, "Translation"))
    arr = _out(tr, "Instances")

    # sample instance transforms to extrapolate the cap positions
    itf = b.node("GeometryNodeInstanceTransform", x0 + 800, -700)
    cnt = b.node("GeometryNodeAttributeDomainSize", x0 + 800, -900,
                 component="INSTANCES")
    b.link(arr, _in(cnt, "Geometry"))
    count_out = _out(cnt, "Instance Count")
    last = b.node("ShaderNodeMath", x0 + 1000, -900, operation="SUBTRACT")
    b.link(count_out, last.inputs[0])
    last.inputs[1].default_value = 1
    prev = b.node("ShaderNodeMath", x0 + 1000, -1050, operation="SUBTRACT")
    b.link(count_out, prev.inputs[0])
    prev.inputs[1].default_value = 2

    def sample(index_socket, index_value, y):
        n = b.node("GeometryNodeSampleIndex", x0 + 1200, y,
                   data_type="FLOAT4X4", domain="INSTANCE", clamp=True)
        b.link(arr, _in(n, "Geometry"))
        b.link(_out(itf, "Transform"), _in(n, "Value"))
        if index_socket is not None:
            b.link(index_socket, _in(n, "Index"))
        else:
            _in(n, "Index").default_value = index_value
        return _out(n, "Value")

    def matmul(a, c, x, y):
        n = b.node("FunctionNodeMatrixMultiply", x, y)
        b.link(a, n.inputs[0])
        b.link(c, n.inputs[1])
        return n.outputs[0]

    def invert(m, x, y):
        n = b.node("FunctionNodeInvertMatrix", x, y)
        b.link(m, n.inputs[0])
        return n.outputs[0]

    t_first = sample(None, 0, -700)
    t_second = sample(None, 1, -850)
    t_prev = sample(prev.outputs[0], 0, -1000)
    t_last = sample(last.outputs[0], 0, -1150)

    # start cap: one step before the first copy; end cap: one step after last
    d_start = matmul(invert(t_first, x0 + 1400, -700), t_second,
                     x0 + 1600, -700)
    t_start = matmul(t_first, invert(d_start, x0 + 1800, -700), x0 + 2000, -700)
    d_end = matmul(invert(t_prev, x0 + 1400, -1050), t_last, x0 + 1600, -1050)
    t_end = matmul(t_last, d_end, x0 + 2000, -1050)

    def cap(obj_socket, matrix, index_socket, index_value, y):
        info = b.node("GeometryNodeObjectInfo", x0 + 2200, y,
                      transform_space="RELATIVE")
        b.link(obj_socket, _in(info, "Object"))
        _in(info, "As Instance").default_value = True
        tg = b.node("GeometryNodeTransform", x0 + 2400, y)
        _in(tg, "Mode").default_value = "Matrix"
        b.link(_out(info, "Geometry"), _in(tg, "Geometry"))
        b.link(matrix, _in(tg, "Transform"))
        st = b.node("GeometryNodeStoreNamedAttribute", x0 + 2600, y,
                    data_type="INT", domain="INSTANCE")
        b.link(_out(tg, "Geometry"), _in(st, "Geometry"))
        _in(st, "Name").default_value = INDEX_ATTR
        if index_socket is not None:
            b.link(index_socket, _in(st, "Value"))
        else:
            _in(st, "Value").default_value = index_value
        return _out(st, "Geometry")

    start_geo = cap(G["Start Cap"], t_start, None, -1, -700)
    end_geo = cap(G["End Cap"], t_end, count_out, 0, -1100)

    # tag every copy with its index so it survives Realize Instances
    idx2 = b.node("GeometryNodeInputIndex", x0 + 2200, 200)
    st_idx = b.node("GeometryNodeStoreNamedAttribute", x0 + 2600, 200,
                    data_type="INT", domain="INSTANCE")
    b.link(arr, _in(st_idx, "Geometry"))
    _in(st_idx, "Name").default_value = INDEX_ATTR
    b.link(_out(idx2, "Index"), _in(st_idx, "Value"))

    join = b.node("GeometryNodeJoinGeometry", x0 + 2900, 0)
    for g in (_out(st_idx, "Geometry"), start_geo, end_geo):
        b.link(g, join.inputs[0])
    real = b.node("GeometryNodeRealizeInstances", x0 + 3100, 0)
    b.link(_out(join, "Geometry"), _in(real, "Geometry"))

    # per-copy UV offset
    n_idx = b.node("GeometryNodeInputNamedAttribute", x0 + 3100, -300,
                   data_type="INT")
    _in(n_idx, "Name").default_value = INDEX_ATTR
    seed = b.add_int(G["Seed"], 4, x0 + 3100, -900)
    neg = b.vmath("SCALE", x0 + 3100, -750, G["Random UV Offset"], scale=-1.0)
    r_uv = b.rand("FLOAT_VECTOR", x0 + 3350, -750, seed, neg.outputs[0],
                  G["Random UV Offset"], id_socket=_out(n_idx, "Attribute"))
    uv_step = b.vmath("SCALE", x0 + 3350, -300, G["UV Offset"],
                      scale=_out(n_idx, "Attribute"))

    def uv_stage(geo_socket, name_socket, x, y):
        # Blender's legacy array shifts every UV map; nodes cannot loop over
        # them, so each named map gets its own stage (empty name = skipped).
        n_uv = b.node("GeometryNodeInputNamedAttribute", x, y - 200,
                      data_type="FLOAT_VECTOR")
        b.link(name_socket, _in(n_uv, "Name"))
        s1 = b.vmath("ADD", x + 250, y - 200, _out(n_uv, "Attribute"),
                     uv_step.outputs[0])
        s2 = b.vmath("ADD", x + 450, y - 200, s1.outputs[0], _out(r_uv, "Value"))
        try:
            st = b.node("GeometryNodeStoreNamedAttribute", x + 650, y,
                        data_type="FLOAT2", domain="CORNER")
        except TypeError:
            st = b.node("GeometryNodeStoreNamedAttribute", x + 650, y,
                        data_type="FLOAT_VECTOR", domain="CORNER")
        b.link(geo_socket, _in(st, "Geometry"))
        b.link(name_socket, _in(st, "Name"))
        b.link(s2.outputs[0], _in(st, "Value"))
        b.link(G["UV Offset Enabled"], _in(st, "Selection"))
        return _out(st, "Geometry")

    uv_geo = _out(real, "Geometry")
    for i in range(UV_SLOTS):
        uv_geo = uv_stage(uv_geo, G[f"UV Map {i + 1}"], x0 + 3600 + 800 * i, 0)
    x_end = x0 + 3600 + 800 * UV_SLOTS

    # merge (reuses the built-in Merge / Merge Distance inputs)
    merge = b.node("GeometryNodeMergeByDistance", x_end, 0)
    b.link(uv_geo, _in(merge, "Geometry"))
    b.link(G["Merge Distance"], _in(merge, "Distance"))
    sw = b.node("GeometryNodeSwitch", x_end + 200, 0, input_type="GEOMETRY")
    b.link(G["Merge"], _in(sw, "Switch"))
    b.link(uv_geo, _in(sw, "False"))
    b.link(_out(merge, "Geometry"), _in(sw, "True"))
    result = _out(sw, "Output")

    try:
        rm = b.node("GeometryNodeRemoveAttribute", x_end + 400, 0)
        b.link(result, _in(rm, "Geometry"))
        _in(rm, "Name").default_value = INDEX_ATTR
        result = _out(rm, "Geometry")
    except Exception:
        pass

    links.new(result, go.inputs[0])
    return tree


def get_node_group():
    existing = bpy.data.node_groups.get(GROUP_NAME)
    if existing is not None:
        if existing.get(REVISION_KEY) == GROUP_REVISION:
            return existing
        # Outdated group saved in this .blend: keep it for old modifiers,
        # but build a fresh one for new ones.
        existing.name = f"{GROUP_NAME} (old)"
    return build_node_group()


# --------------------------------------------------------------------------
# Operator / UI
# --------------------------------------------------------------------------

def set_modifier_input(mod, identifier, value):
    """Set a Geometry Nodes modifier input (API differs between versions)."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        try:
            props.inputs[identifier]["value"] = value  # Blender 5.2+
            mod.id_data.update_tag()
            return
        except (KeyError, TypeError):
            pass
    mod[identifier] = value  # Blender 4.2 - 5.1
    mod.id_data.update_tag()


def is_advanced_array(mod):
    return mod.type == "NODES" and mod.node_group is not None \
        and mod.node_group.name.startswith(GROUP_NAME)


def sync_uv_maps(obj, mod):
    """Point the UV slots at the mesh's UV maps, so every map is offset (like
    the legacy array, which has no UV map field)."""
    names = [uv.name for uv in obj.data.uv_layers][:UV_SLOTS] or ["UVMap"]
    slots = {}
    for it in mod.node_group.interface.items_tree:
        if it.item_type == "SOCKET" and it.in_out == "INPUT" \
                and it.name.startswith("UV Map "):
            slots[it.name] = it.identifier
    for i in range(UV_SLOTS):
        key = f"UV Map {i + 1}"
        if key in slots:
            set_modifier_input(mod, slots[key],
                               names[i] if i < len(names) else "")


def add_advanced_array(obj):
    mod = obj.modifiers.new("Advanced Array", "NODES")
    mod.node_group = get_node_group()
    sync_uv_maps(obj, mod)
    obj.update_tag()
    return mod


class OBJECT_OT_sync_advanced_array_uvs(bpy.types.Operator):
    bl_idname = "object.sync_advanced_array_uvs"
    bl_label = "Sync UV Maps"
    bl_description = ("Apply the UV offset to all of the mesh's UV maps "
                      "(run again after adding or renaming UV maps)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "MESH" \
            and any(is_advanced_array(m) for m in obj.modifiers)

    def execute(self, context):
        obj = context.object
        for mod in obj.modifiers:
            if is_advanced_array(mod):
                sync_uv_maps(obj, mod)
        return {"FINISHED"}


class OBJECT_OT_add_advanced_array(bpy.types.Operator):
    bl_idname = "object.add_advanced_array"
    bl_label = "Advanced Array"
    bl_description = "Add an array modifier with UV offset, caps and randomization"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def execute(self, context):
        targets = [o for o in context.selected_objects if o.type == "MESH"] \
            or [context.object]
        try:
            for obj in targets:
                add_advanced_array(obj)
        except RuntimeError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        return {"FINISHED"}


def _menu_func(self, context):
    self.layout.separator()
    self.layout.operator(OBJECT_OT_add_advanced_array.bl_idname,
                         icon="MOD_ARRAY")


class VIEW3D_PT_advanced_array(bpy.types.Panel):
    bl_label = "Advanced Array"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Advanced Array"

    def draw(self, context):
        col = self.layout.column()
        col.operator(OBJECT_OT_add_advanced_array.bl_idname, icon="MOD_ARRAY")
        col.operator(OBJECT_OT_sync_advanced_array_uvs.bl_idname, icon="UV")
        col.label(text="Settings live in the modifier tab.")


_classes = (OBJECT_OT_add_advanced_array, OBJECT_OT_sync_advanced_array_uvs,
            VIEW3D_PT_advanced_array)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    if hasattr(bpy.types, "OBJECT_MT_modifier_add"):
        bpy.types.OBJECT_MT_modifier_add.append(_menu_func)


def unregister():
    if hasattr(bpy.types, "OBJECT_MT_modifier_add"):
        bpy.types.OBJECT_MT_modifier_add.remove(_menu_func)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
