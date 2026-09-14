#!/usr/bin/env python3
from pathlib import Path
import ast
root=Path(__file__).parents[1]/"migrations/versions"
revisions={}
for path in sorted(root.glob("*.py")):
 tree=ast.parse(path.read_text())
 vals={}
 for node in tree.body:
  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"revision","down_revision"}:
   vals[node.targets[0].id]=ast.literal_eval(node.value)
 if "revision" in vals: revisions[vals["revision"]]=vals.get("down_revision")
parents = {parent for value in revisions.values() for parent in (value if isinstance(value, (tuple, list, set)) else (value,)) if isinstance(parent, str)}
heads=[r for r in revisions if r not in parents]
if len(heads)!=1: raise SystemExit(f"expected one migration head, found {heads}")
print(f"valid migration chain head={heads[0]} revisions={len(revisions)}")
