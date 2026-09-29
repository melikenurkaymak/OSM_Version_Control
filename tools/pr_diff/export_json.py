#!/usr/bin/env python3
"""
Compares two .osm files (before/after) and produces the compact JSON that
viewer_template.html needs.

Usage:
    python3 export_json.py <before.osm> <after.osm> <output.json>
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auto_diff import load, classify_changes, way_coords, tag_diff_lines

before_path, after_path, out_path = sys.argv[1:4]
before, after = load(before_path), load(after_path)
geo_ways, attr_ways = classify_changes(before, after)


def r(pts):
    return [[round(x, 2), round(y, 2)] for x, y in pts]


ways_after = {}
for wid, w in after["ways"].items():
    pts = way_coords(after, wid)
    if len(pts) >= 2:
        ways_after[wid] = {"t": w["tags"].get("type", ""), "p": r(pts)}

ways_before_only = {}
added_ids, removed_ids = [], []
for wid, kind in geo_ways.items():
    if kind == "added":
        added_ids.append(wid)
    elif kind == "removed":
        removed_ids.append(wid)
        pts = way_coords(before, wid)
        wtag = before["ways"][wid]["tags"].get("type", "")
        if len(pts) >= 2:
            ways_before_only[wid] = {"t": wtag, "p": r(pts)}
    else:
        pts = way_coords(before, wid)
        wtag = before["ways"][wid]["tags"].get("type", "")
        if len(pts) >= 2:
            ways_before_only[wid] = {"t": wtag, "p": r(pts)}

geo_changed_ids = [wid for wid, k in geo_ways.items() if k == "geometry"]

# clustering (same logic as auto_diff.py)
centroids = {}
for wid in geo_ways:
    pts = way_coords(after, wid) or way_coords(before, wid)
    if pts:
        centroids[wid] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

CLUSTER_DIST = 40.0
parent = {wid: wid for wid in centroids}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


ids = list(centroids)
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        (x1, y1), (x2, y2) = centroids[ids[i]], centroids[ids[j]]
        if ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 <= CLUSTER_DIST:
            union(ids[i], ids[j])

clusters_map = {}
for wid in ids:
    clusters_map.setdefault(find(wid), []).append(wid)

clusters = []
for wids in sorted(clusters_map.values(), key=len, reverse=True):
    pts = []
    for wid in wids:
        pts += way_coords(after, wid) or way_coords(before, wid)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    clusters.append({
        "ids": wids,
        "bbox": [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
        "kinds": [geo_ways[w] for w in wids],
    })

attr_details = []
for wid in attr_ways:
    diffs = tag_diff_lines(before, after, wid)
    wtype = after["ways"].get(wid, {}).get("tags", {}).get("type", "?")
    attr_details.append({"id": wid, "type": wtype, "diff": diffs})

# Ways with the "centerline" role, and the other relation-derived roles
# (crosswalk boundary, right_of_way priority/yield boundary) -- union of
# before/after, since a role tied to an added/removed lanelet may only
# exist on one side.
centerline_ids = sorted(before.get("centerline_ids", set()) | after.get("centerline_ids", set()))
crosswalk_ids = sorted(before.get("crosswalk_ids", set()) | after.get("crosswalk_ids", set()))
priority_ids = sorted(before.get("priority_ids", set()) | after.get("priority_ids", set()))
yield_ids = sorted(before.get("yield_ids", set()) | after.get("yield_ids", set()))

data = {
    "waysAfter": ways_after,
    "waysBeforeOnly": ways_before_only,
    "addedIds": added_ids,
    "removedIds": removed_ids,
    "geoChangedIds": geo_changed_ids,
    "centerlineIds": centerline_ids,
    "crosswalkIds": crosswalk_ids,
    "priorityIds": priority_ids,
    "yieldIds": yield_ids,
    "clusters": clusters,
    "attrDetails": attr_details,
    "stats": {
        "totalWays": len(ways_after),
        "geoChanges": len(geo_ways),
        "attrChanges": len(attr_ways),
    },
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(data, f, separators=(",", ":"))

print(f"written: {out_path}")
