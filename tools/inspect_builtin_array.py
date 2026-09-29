"""Dev helper: print the interface and output chain of Blender's bundled
"Array" node group, which Advanced Array is built on.

    blender -b --factory-startup --python tools/inspect_builtin_array.py

Run this after a Blender upgrade if the extension stops building; it shows
which sockets and nodes the bundled group has now.
"""
import os

import bpy

path = os.path.join(bpy.utils.system_resource("DATAFILES"),
                    "assets", "nodes", "geometry_nodes_essentials.blend")
with bpy.data.libraries.load(path) as (src, dst):
    dst.node_groups = ["Array"]
ng = dst.node_groups[0]

print("=== interface ===")
for it in ng.interface.items_tree:
    if it.item_type == "PANEL":
        print(f"[panel] {it.name}")
    else:
        print(f"  {it.in_out[:3]} {it.name}: {it.socket_type}  ({it.identifier})")

print("=== menu switches ===")
for n in ng.nodes:
    if n.bl_idname == "GeometryNodeMenuSwitch":
        print(f"  {n.name}: {[i.name for i in n.enum_items]}")

print("=== geometry chain feeding the group output ===")
go = next(n for n in ng.nodes if n.bl_idname == "NodeGroupOutput")


def trace(sock, depth=0, seen=None):
    seen = set() if seen is None else seen
    for l in ng.links:
        if l.to_socket == sock:
            n = l.from_node
            print("  " * depth + f"<- {n.bl_idname} '{n.name}'")
            if depth < 6 and n.name not in seen:
                seen.add(n.name)
                for s in n.inputs:
                    if s.type == "GEOMETRY" and s.is_linked:
                        trace(s, depth + 1, seen)


trace(go.inputs[0])
