"""Portable numeric node templates; placed nodes embed their full definition."""
import copy
import json
from pathlib import Path

from graph_core import GraphError,KINDS,Node,validate_props


def validate_template(props):
    validate_props(Node("n1","Custom",0,0,props))
    if props["template_name"].casefold() in {kind.casefold() for kind in KINDS}:
        raise GraphError("Choose a name different from a built-in node, such as Add tax.")
    return copy.deepcopy(props)


def load_templates(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise GraphError("The custom node library is too large (1 MB max).")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError,UnicodeError) as exc:
        raise GraphError(f"Cannot read custom nodes: {exc}") from None
    if not isinstance(data,dict) or data.get("version") != 1 or not isinstance(data.get("nodes"),list) or len(data["nodes"]) > 100:
        raise GraphError("Unsupported custom node library. Expected up to 100 node definitions.")
    templates = {}
    seen = set()
    for item in data["nodes"]:
        if not isinstance(item,dict):
            raise GraphError("A saved custom node is not a valid definition.")
        props = validate_template(item)
        name = props["template_name"]
        if name.casefold() in seen:
            raise GraphError(f"Duplicate custom node name: {name}")
        seen.add(name.casefold())
        templates[name] = props
    return templates


def save_template(path, templates, props):
    validated = validate_template(props)
    name = validated["template_name"]
    path = Path(path)
    if path.exists():
        templates = load_templates(path)
    if name.casefold() in {key.casefold() for key in templates}:
        raise GraphError(f"A node named '{name}' is already saved. Choose a new name.")
    if len(templates) >= 100:
        raise GraphError("The library already has 100 custom nodes.")
    updated = {key:validate_template(value) for key,value in templates.items()}
    updated[name] = validated
    temporary = path.with_name(path.name+".tmp")
    try:
        temporary.write_text(json.dumps({"version":1,"nodes":list(updated.values())},indent=2),encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return updated
