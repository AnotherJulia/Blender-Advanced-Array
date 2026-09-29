"""Headless test for the Advanced Array extension.

Run from the repository root:

    blender -b --factory-startup --python tests/test_advanced_array.py

Optionally test a built extension zip instead of the source folder:

    blender -b --factory-startup --python tests/test_advanced_array.py -- dist/advanced_array-0.2.1.zip
"""
import importlib.util
import os
import sys
import tempfile
import zipfile

import bpy

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if "--" in sys.argv and sys.argv[sys.argv.index("--") + 1:]:
    tmp = tempfile.mkdtemp()
    zipfile.ZipFile(sys.argv[sys.argv.index("--") + 1]).extractall(tmp)
    init_path = os.path.join(tmp, "__init__.py")
else:
    init_path = os.path.join(ROOT, "src", "__init__.py")

spec = importlib.util.spec_from_file_location("advanced_array", init_path)
aa = importlib.util.module_from_spec(spec)
sys.modules["advanced_array"] = aa
spec.loader.exec_module(aa)
aa.register()

bpy.ops.wm.read_factory_settings(use_empty=True)


def fresh_cube(name="Cube", size=2.0, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.object
    o.name = name
    return o


def setup():
    cube = fresh_cube()
    m = aa.add_advanced_array(cube)
    ids = {}
    for it in m.node_group.interface.items_tree:
        if it.item_type == "SOCKET" and it.in_out == "INPUT":
            ids.setdefault(it.name, it.identifier)

    def setv(name, value):
        aa.set_modifier_input(m, ids[name], value)

    def result():
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        return cube.evaluated_get(dg).to_mesh()

    return cube, m, setv, result


def avg_x(me, lo, hi):
    vs = me.vertices[lo:hi]
    return sum(v.co.x for v in vs) / len(vs)


fails = []


def check(label, cond, info=""):
    print(("PASS " if cond else "FAIL ") + label, info)
    if not cond:
        fails.append(label)


# --- 0. an outdated group saved in a .blend gets replaced ------------------
old = bpy.data.node_groups.new(aa.GROUP_NAME, "GeometryNodeTree")  # no revision
cube0 = fresh_cube("Cube0")
fresh = aa.add_advanced_array(cube0).node_group
check("outdated group replaced by current revision",
      fresh is not old and fresh.get(aa.REVISION_KEY) == aa.GROUP_REVISION
      and old.name.endswith("(old)"), (fresh.name, old.name))
bpy.data.objects.remove(cube0)
bpy.data.node_groups.remove(old)
bpy.data.node_groups.remove(fresh)

# --- 1. basics: count, relative offset (Offset Method 2 == Relative) ------
cube, m, setv, result = setup()
setv("Count", 4)
setv("Offset Method", 2)
setv("Offset", (1, 0, 0))
me = result()
check("count 4 -> 32 verts", len(me.vertices) == 32, len(me.vertices))
check("relative offset spacing 2.0",
      abs(avg_x(me, 8, 16) - avg_x(me, 0, 8) - 2.0) < 1e-4,
      [round(avg_x(me, i * 8, i * 8 + 8), 3) for i in range(4)])

# --- 2. UV offset ---------------------------------------------------------
setv("UV Offset", (0.25, 0, 0))
me = result()
uv = me.uv_layers.get("UVMap")
check("UVMap present", uv is not None)
if uv:
    per = len(uv.data) // 4
    mins = [min(d.uv.x for d in uv.data[i * per:(i + 1) * per]) for i in range(4)]
    check("UV offset per copy", abs(mins[1] - mins[0] - 0.25) < 1e-4
          and abs(mins[3] - mins[0] - 0.75) < 1e-4, [round(x, 3) for x in mins])

# --- 2b. every UV map is offset once synced (legacy behaviour) -------------
cube.data.uv_layers.new(name="Second")
aa.sync_uv_maps(cube, m)
me = result()


def u_shifts(name):
    layer = me.uv_layers[name]
    per = len(layer.data) // 4
    mins = [min(d.uv.x for d in layer.data[i * per:(i + 1) * per]) for i in range(4)]
    return [round(x - mins[0], 3) for x in mins]


check("second UV map offset too", u_shifts("Second") == [0.0, 0.25, 0.5, 0.75]
      and u_shifts("UVMap") == [0.0, 0.25, 0.5, 0.75],
      (u_shifts("UVMap"), u_shifts("Second")))

# --- 3. randomize actually varies ------------------------------------------
setv("UV Offset", (0, 0, 0))
setv("Randomize", True)
setv("Randomize Offset", (0, 1.0, 0))
setv("Seed", 3)
me = result()
ys = [sum(v.co.y for v in me.vertices[i * 8:(i + 1) * 8]) / 8 for i in range(4)]
check("random offset varies copies (excl. first)", len({round(y, 3) for y in ys[1:]}) > 1
      and abs(ys[0]) < 1e-6, [round(y, 3) for y in ys])
setv("Randomize", False)

# --- 4. merge ---------------------------------------------------------------
setv("Offset Method", 0)              # constant translation
setv("Translation", (2.0, 0, 0))      # cubes touch exactly
me = result()
n_unmerged = len(me.vertices)
setv("Merge", True)
me = result()
check("merge welds touching cubes", len(me.vertices) < n_unmerged,
      f"{n_unmerged} -> {len(me.vertices)}")
setv("Merge", False)

# --- 5. Add Constant Offset ------------------------------------------------
setv("Offset Method", 2)
setv("Offset", (1, 0, 0))
setv("Add Constant Offset", True)
setv("Extra Constant Offset", (0.5, 0, 0))
me = result()
check("relative + constant combined (2.5 spacing)",
      abs(avg_x(me, 8, 16) - avg_x(me, 0, 8) - 2.5) < 1e-4,
      [round(avg_x(me, i * 8, i * 8 + 8), 3) for i in range(4)])
setv("Add Constant Offset", False)

# --- 6. caps ----------------------------------------------------------------
cap_s = fresh_cube("CapStart", 1.0)
cap_e = fresh_cube("CapEnd", 1.0)
bpy.context.view_layer.objects.active = cube
setv("Count", 3)
setv("Start Cap", cap_s)
setv("End Cap", cap_e)
me = result()
check("caps add 2 cubes -> 40 verts", len(me.vertices) == 40, len(me.vertices))
if len(me.vertices) == 40:
    centers = sorted(round(avg_x(me, i * 8, i * 8 + 8), 2) for i in range(5))
    check("copies at 0,2,4 with caps at -2 and +6", centers == [-2.0, 0.0, 2.0, 4.0, 6.0],
          centers)

# --- 7. shapes / fit modes run without error ---------------------------------
setv("Start Cap", None)
setv("End Cap", None)
setv("Shape", 1)   # circle
me = result()
check("circle shape produces geometry", len(me.vertices) > 0, len(me.vertices))
setv("Shape", 0)
setv("Count Method", 1)  # distance
setv("Distance", 3.0)
me = result()
check("count method 'Distance' produces geometry", len(me.vertices) > 0, len(me.vertices))

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
