#!/usr/bin/env python3
"""
Embeds the JSON produced by export_json.py into viewer_template.html to
produce a single self-contained HTML report that makes no outbound requests.

Usage:
    python3 build_report.py viewer_template.html diff.json report.html
"""
import sys

template_path, json_path, out_path = sys.argv[1:4]

with open(template_path, encoding="utf-8") as f:
    tpl = f.read()
with open(json_path, encoding="utf-8") as f:
    data = f.read()

# Escape any literal </script> inside the JSON so it can't break the HTML
data_safe = data.replace("</script>", "<\\/script>")
out = tpl.replace("__DATA_JSON__", data_safe)

with open(out_path, "w", encoding="utf-8") as f:
    f.write(out)

print(f"written: {out_path} ({len(out)} bytes)")
