#!/usr/bin/env python3
"""
Compares two .osm files and automatically FINDS which ways/nodes changed
(way ids / coordinates are never given manually). Geometry (shape) changes
are rendered visually; pure tag/attribute changes are reported as separate
text output -- so bulk/automated tag additions (e.g. traffic_light_v2x_id)
don't clutter the image.
"""
import sys
import xml.etree.ElementTree as ET

COLORS = {
    "lanelet": "#2563eb",
    "stop_line": "#f59e0b",
    "traffic_light": "#dc2626",
    "centerline": "#0891b2",
    "changed": "#16a34a",
    "crosswalk": "#7c3aed",
    "speed_bump": "#db2777",
    "bus_stop_line": "#ca8a04",
    "priority": "#0d9488",
    "yield": "#ea580c",
}
EPS = 1e-6


def load(path):
    root = ET.parse(path).getroot()
    nodes = {}
    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if "local_x" in tags and "local_y" in tags:
            nodes[n.get("id")] = (float(tags["local_x"]), float(tags["local_y"]))
        else:
            lon, lat = float(n.get("lon")), float(n.get("lat"))
            if lon or lat:
                nodes[n.get("id")] = (lon, lat)

    ways = {}
    for w in root.findall("way"):
        refs = [nd.get("ref") for nd in w.findall("nd")]
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        ways[w.get("id")] = {"refs": refs, "tags": tags}

    # Relations carry information no way's own tags have:
    #  - "centerline": which way is a lanelet's centerline member.
    #  - crosswalk: a lanelet relation tagged subtype=crosswalk has no
    #    centerline, just left/right boundary ways -- those are what we
    #    highlight as the crosswalk.
    #  - right_of_way: a SEPARATE relation (type=regulatory_element,
    #    subtype=right_of_way) that references other LANELETS (by relation
    #    id, not way id) with role="right_of_way" (has priority) or
    #    role="yield" (must yield). It carries no geometry of its own, so we
    #    resolve it to the left/right boundary ways of the lanelets it
    #    references. A right_of_way relation can appear before or after the
    #    lanelets it points at, so lanelet boundaries are indexed first and
    #    right_of_way relations resolved in a second pass below.
    centerline_ids = set()
    crosswalk_ids = set()
    lanelet_boundary_ids = {}  # lanelet relation id -> its left/right way ids
    row_relations = []  # list of (priority_lanelet_ids, yield_lanelet_ids)

    for rel in root.findall("relation"):
        rel_tags = {t.get("k"): t.get("v") for t in rel.findall("tag")}
        rtype = rel_tags.get("type")

        if rtype == "lanelet":
            boundary_ids = set()
            for m in rel.findall("member"):
                if m.get("type") != "way":
                    continue
                role = m.get("role")
                if role == "centerline":
                    centerline_ids.add(m.get("ref"))
                elif role in ("left", "right"):
                    boundary_ids.add(m.get("ref"))
            lanelet_boundary_ids[rel.get("id")] = boundary_ids
            if rel_tags.get("subtype") == "crosswalk":
                crosswalk_ids |= boundary_ids

        elif rtype == "regulatory_element" and rel_tags.get("subtype") == "right_of_way":
            priority_lanelets = {m.get("ref") for m in rel.findall("member")
                                  if m.get("type") == "relation" and m.get("role") == "right_of_way"}
            yield_lanelets = {m.get("ref") for m in rel.findall("member")
                               if m.get("type") == "relation" and m.get("role") == "yield"}
            row_relations.append((priority_lanelets, yield_lanelets))

    priority_ids, yield_ids = set(), set()
    for priority_lanelets, yield_lanelets in row_relations:
        for lid in priority_lanelets:
            priority_ids |= lanelet_boundary_ids.get(lid, set())
        for lid in yield_lanelets:
            yield_ids |= lanelet_boundary_ids.get(lid, set())

    return {
        "nodes": nodes,
        "ways": ways,
        "centerline_ids": centerline_ids,
        "crosswalk_ids": crosswalk_ids,
        "priority_ids": priority_ids,
        "yield_ids": yield_ids,
    }


def classify_changes(before, after):
    changed_node_ids = set()
    for nid, coord in after["nodes"].items():
        if nid not in before["nodes"] or any(
            abs(a - b) > EPS for a, b in zip(coord, before["nodes"][nid])
        ):
            changed_node_ids.add(nid)
    changed_node_ids |= set(before["nodes"]) - set(after["nodes"])

    geo_ways, attr_ways = {}, {}
    for wid in set(before["ways"]) | set(after["ways"]):
        wb, wa = before["ways"].get(wid), after["ways"].get(wid)
        if wb is None:
            geo_ways[wid] = "added"
            continue
        if wa is None:
            geo_ways[wid] = "removed"
            continue
        geo_changed = wb["refs"] != wa["refs"] or any(r in changed_node_ids for r in wa["refs"])
        tag_changed = wb["tags"] != wa["tags"]
        if geo_changed:
            geo_ways[wid] = "geometry"
        elif tag_changed:
            attr_ways[wid] = "tag"

    return geo_ways, attr_ways


def way_coords(data, wid):
    w = data["ways"].get(wid)
    if not w:
        return []
    return [data["nodes"][r] for r in w["refs"] if r in data["nodes"]]


def tag_diff_lines(before, after, wid):
    wb, wa = before["ways"].get(wid), after["ways"].get(wid)
    lines = []
    tb, ta = (wb or {}).get("tags", {}), (wa or {}).get("tags", {})
    for k in sorted(set(tb) | set(ta)):
        if tb.get(k) != ta.get(k):
            if k not in tb:
                lines.append(f"+{k}={ta[k]}")
            elif k not in ta:
                lines.append(f"-{k}")
            else:
                lines.append(f"{k}:{tb[k]}->{ta[k]}")
    return lines


def node_move_lines(before, after, wid):
    wb, wa = before["ways"].get(wid), after["ways"].get(wid)
    lines = []
    if not wb or not wa:
        return lines
    for r in wa["refs"]:
        if r in wb["refs"] and r in before["nodes"] and r in after["nodes"]:
            bx, by = before["nodes"][r]
            ax_, ay = after["nodes"][r]
            if abs(bx - ax_) > EPS or abs(by - ay) > EPS:
                dist = ((ax_ - bx) ** 2 + (ay - by) ** 2) ** 0.5
                lines.append(f"node {r} moved {dist:.2f} units")
    if wb["refs"] != wa["refs"]:
        lines.append(f"node sequence {len(wb['refs'])}->{len(wa['refs'])}")
    return lines


def plot(data, title, ax, highlight_ids, xlim, ylim):
    centerline_ids = data.get("centerline_ids", set())
    crosswalk_ids = data.get("crosswalk_ids", set())
    priority_ids = data.get("priority_ids", set())
    yield_ids = data.get("yield_ids", set())
    for wid, w in data["ways"].items():
        coords = way_coords(data, wid)
        if len(coords) < 2 or wid in highlight_ids:
            continue
        xs, ys = zip(*coords)
        t = w["tags"].get("type")
        if wid in yield_ids:
            ax.plot(xs, ys, color=COLORS["yield"], linewidth=1.6, zorder=3)
        elif wid in priority_ids:
            ax.plot(xs, ys, color=COLORS["priority"], linewidth=1.6, zorder=3)
        elif wid in crosswalk_ids:
            ax.plot(xs, ys, color=COLORS["crosswalk"], linewidth=1.8, zorder=3)
        elif wid in centerline_ids:
            ax.plot(xs, ys, color=COLORS["centerline"], linewidth=1.3, zorder=2,
                     linestyle=(0, (4, 3)), dash_capstyle="round")
        elif t == "traffic_light":
            ax.plot(xs, ys, color=COLORS["traffic_light"], linewidth=2.2, zorder=4)
        elif t == "stop_line":
            ax.plot(xs, ys, color=COLORS["stop_line"], linewidth=1.8, zorder=3)
        elif t == "speed_bump":
            ax.plot(xs, ys, color=COLORS["speed_bump"], linewidth=1.8, zorder=3)
        elif t == "bus_stop_line":
            ax.plot(xs, ys, color=COLORS["bus_stop_line"], linewidth=1.8, zorder=3)
        else:
            ax.plot(xs, ys, color=COLORS["lanelet"], linewidth=0.7, zorder=1, alpha=0.6)

    for wid in highlight_ids:
        coords = way_coords(data, wid)
        if len(coords) < 2:
            continue
        xs, ys = zip(*coords)
        ax.plot(xs, ys, color=COLORS["changed"], linewidth=4, zorder=10, solid_capstyle="round")

    ax.set_title(title, fontsize=11)
    ax.set_aspect("equal")
    ax.axis("off")
    if xlim:
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)


if __name__ == "__main__":
    # matplotlib is only needed for this standalone CLI (it renders PNG
    # region images); the CI-facing export_json.py / build_report.py path
    # never hits this block, so it never needs matplotlib installed.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    before_path, after_path, out_path = sys.argv[1:4]
    before, after = load(before_path), load(after_path)
    geo_ways, attr_ways = classify_changes(before, after)

    if not geo_ways and not attr_ways:
        print("No differences found, files are identical.")
        sys.exit(0)

    print(f"\n== Ways with geometry changes: {len(geo_ways)} ==")
    for wid, kind in sorted(geo_ways.items(), key=lambda x: int(x[0])):
        way_type = (after["ways"].get(wid) or before["ways"].get(wid))["tags"].get("type", "?")
        extra = ", ".join(node_move_lines(before, after, wid) + tag_diff_lines(before, after, wid))
        print(f"  way {wid} ({way_type}) [{kind}]" + (f": {extra}" if extra else ""))

    print(f"\n== Ways with tag/attribute-only changes: {len(attr_ways)} ==")
    tag_groups = {}
    for wid in attr_ways:
        diffs = tuple(sorted(tag_diff_lines(before, after, wid)))
        tag_groups.setdefault(diffs, []).append(wid)
    for diffs, wids in tag_groups.items():
        print(f"  {len(wids)} way(s): {', '.join(diffs)}  (e.g. way {wids[0]}{' ...' if len(wids) > 1 else ''})")

    if not geo_ways:
        print("\nNo geometry (shape) changes -- the image will show NO visible diff, "
              "since all changes are tag/attribute-level. See the text report above.")
        sys.exit(0)

    # Group changes in distant regions into separate "clusters" (simple
    # distance-threshold union-find)
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

    clusters = {}
    for wid in ids:
        clusters.setdefault(find(wid), []).append(wid)
    cluster_list = sorted(clusters.values(), key=len, reverse=True)
    print(f"\nDetected {len(cluster_list)} separate region(s) with geometry changes; generating one image per region.")

    base = out_path.rsplit(".", 1)
    stem, ext = (base[0], base[1]) if len(base) == 2 else (out_path, "png")

    for idx, way_ids in enumerate(cluster_list, start=1):
        pts = []
        for wid in way_ids:
            pts += way_coords(after, wid) or way_coords(before, wid)
        margin = 10.0
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        xlim = (min(xs) - margin, max(xs) + margin)
        ylim = (min(ys) - margin, max(ys) + margin)

        fig, axes = plt.subplots(1, 2, figsize=(13, 6.5))
        plot(before, "Before", axes[0], way_ids, xlim, ylim)
        plot(after, "After", axes[1], way_ids, xlim, ylim)

        legend = [
            plt.Line2D([0], [0], color=COLORS["lanelet"], lw=1.2, label="Lanelet boundary"),
            plt.Line2D([0], [0], color=COLORS["centerline"], lw=1.3, linestyle=(0, (4, 3)), label="Centerline"),
            plt.Line2D([0], [0], color=COLORS["crosswalk"], lw=1.6, label="Crosswalk"),
            plt.Line2D([0], [0], color=COLORS["speed_bump"], lw=1.6, label="Speed bump"),
            plt.Line2D([0], [0], color=COLORS["bus_stop_line"], lw=1.6, label="Bus stop"),
            plt.Line2D([0], [0], color=COLORS["priority"], lw=1.6, label="Right of way"),
            plt.Line2D([0], [0], color=COLORS["yield"], lw=1.6, label="Yield"),
            plt.Line2D([0], [0], color=COLORS["changed"], lw=4, label=f"Changed object ({len(way_ids)})"),
        ]
        fig.legend(handles=legend, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.05))
        fig.suptitle(f"Region {idx}/{len(cluster_list)} · way: {', '.join(way_ids)}", fontsize=9, y=0.02)
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])

        cluster_out = out_path if len(cluster_list) == 1 else f"{stem}_region{idx}.{ext}"
        plt.savefig(cluster_out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print("saved:", cluster_out)
