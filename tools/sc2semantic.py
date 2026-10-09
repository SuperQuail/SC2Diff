# -*- coding: utf-8 -*-
"""sc2semantic -- order-independent, identity-keyed diff for StarCraft II document components.

The editor rewrites a component wholesale and sibling order is not meaningful, so a line
diff reports large churn for a one-field edit.  Every component gets a canonical model
keyed by identity:

  Objects                     <PlacedObjects>  entity = Id              (ObjectUnit / ObjectDoodad)
  Triggers                    <TriggerData>    entity = Id              (Element) + Root items
  Base.SC2Data/GameData/*.xml <Catalog>        entity = <tag>:<id>      (CUnit / CAbil / ...)
  t3Terrain.xml, ComponentList, Preload.xml    generic keyed flattening
  LocalizedData/*.txt                          key=value pairs
  *.galaxy, PreloadAssetDB.txt                 normalized lines
  DocumentHeader, t3*, assets                  binary (hash + size + hex preview)

Models are plain dicts, so diffing is a dict comparison and ignores ordering.
"""
from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
XML_NAMES = {
    "Objects", "Triggers", "DocumentInfo", "ComponentList.SC2Components",
    "MapInfo", "Preload.xml", "CustomAI", "BankList.xml", "t3Terrain.xml",
}
TEXT_NAMES = {"(listfile)", "PreloadAssetDB.txt"}


def classify(name: str, data: bytes) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    if name in XML_NAMES or base in XML_NAMES or base.endswith(".xml") \
            or base.endswith(".SC2Components") or base.endswith(".SC2Layout") \
            or base.endswith(".SC2Style"):
        return "xml"
    if base.endswith(".galaxy"):
        return "galaxy"
    if base.endswith(".version"):
        return "version"
    if base.endswith(".txt") or base.endswith(".SC2Locale"):
        return "text"
    if data[:5] == b"<?xml":
        return "xml"
    if len(data) >= 4 and data[:4] == b"cdes":
        return "version"
    if data and b"\x00" not in data[:512]:
        try:
            data[:512].decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            pass
    return "binary"


# --------------------------------------------------------------------------- #
# generic XML flattening: entity -> field path -> value
# --------------------------------------------------------------------------- #
def _attrs(elem) -> str:
    parts = []
    for k in sorted(elem.attrib):
        parts.append('%s="%s"' % (k, elem.attrib[k]))
    return " ".join(parts)


def _entity_key(elem) -> str:
    for attr in ("Id", "id", "index", "Index", "Type", "type", "Link", "Name"):
        if attr in elem.attrib:
            return "%s:%s" % (elem.tag, elem.attrib[attr])
    return elem.tag


IDENTITY_ATTRS = ("Index", "index", "Id", "id", "Type", "type", "Name", "name", "Link", "link")


def _identity(child):
    """A stable label for a child element, preferring a semantic identity over its position.

    This is what makes the diff order-independent: positional keys (Tag[3]) shift whenever an
    earlier sibling is inserted or deleted, which turns a one-object edit into hundreds of
    spurious field changes.
    """
    for attr in IDENTITY_ATTRS:
        if attr in child.attrib:
            return "%s[%s=%s]" % (child.tag, attr, child.attrib[attr])
    return None


def _path_key(elem, index_map) -> str:
    """Stable path for a descendant element."""
    chain = []
    node = elem
    while node is not None:
        parent, label = index_map[id(node)]
        if label is not None:
            chain.append(label)
        node = parent
    return "/".join(reversed(chain))


def _build_index(root):
    """Map id(elem) -> (parent, label).  Label is the identity attribute form when the
    element carries one, otherwise Tag[n] only if it is ambiguous among its siblings."""
    index_map = {}
    def walk(node):
        children = list(node)
        counts = {}
        for child in children:
            counts[child.tag] = counts.get(child.tag, 0) + 1
        seen = {}
        for child in children:
            seen.setdefault(child.tag, 0)
            ordinal = seen[child.tag]
            seen[child.tag] += 1
            label = _identity(child)
            if label is None:
                label = child.tag if counts[child.tag] == 1 else "%s[%d]" % (child.tag, ordinal)
            index_map[id(child)] = (node, label)
            walk(child)
    index_map[id(root)] = (None, None)
    walk(root)
    return index_map


def flatten_xml(data: bytes):
    """Return (entities, meta) where entities maps entity key -> {field path: value}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None, None
    index_map = _build_index(root)
    entities = {}
    meta = {"root": root.tag, "attrs": _attrs(root)}

    def fields_of(elem):
        out = {}
        if elem.text and elem.text.strip():
            out["#text"] = elem.text.strip()
        for k in sorted(elem.attrib):
            out["@" + k] = elem.attrib[k]
        for child in elem.iter():
            if child is elem:
                continue
            path = _path_key(child, index_map)
            if child.text and child.text.strip():
                out[path + "/#text"] = child.text.strip()
            for k in sorted(child.attrib):
                out[path + "/@" + k] = child.attrib[k]
        return out

    for child in list(root):
        key = _entity_key(child)
        n = 1
        base = key
        while key in entities:
            n += 1
            key = "%s#%d" % (base, n)
        entities[key] = fields_of(child)
    return entities, meta


# --------------------------------------------------------------------------- #
# canonical models
# --------------------------------------------------------------------------- #
def canon_text(data: bytes):
    lines = []
    for raw in data.decode("utf-8", "replace").splitlines():
        s = raw.strip()
        if not s:
            continue
        lines.append(s)
    return {"type": "text", "lines": lines}


def canon_version(data: bytes):
    """44-byte sidecar: 'cdes' + reversed component type + build number + a Unix timestamp."""
    info = {"type": "version", "bytes": len(data)}
    if len(data) >= 36:
        info["type_tag"] = data[4:8][::-1].decode("latin-1", "replace")
        info["build"] = int.from_bytes(data[8:12], "little")
        info["timestamp"] = int.from_bytes(data[32:36], "little")
    return info


def canonical(name: str, data: bytes) -> dict:
    kind = classify(name, data)
    if kind == "xml":
        entities, meta = flatten_xml(data)
        if entities is not None:
            return {"type": "xml", "kind": "xml", "root": meta["root"],
                    "root_attrs": meta["attrs"], "entities": entities}
        return {"type": "xml-unparsed", "lines": canon_text(data)["lines"]}
    if kind == "version":
        return canon_version(data)
    if kind in ("text", "galaxy"):
        return canon_text(data)
    return {"type": "binary", "bytes": len(data), "sha1": hashlib.sha1(data).hexdigest()[:16],
            "preview": data[:24].hex()}


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #
def _diff_fields(a: dict, b: dict, limit: int = 400) -> list:
    out = []
    for k in sorted(set(a) | set(b)):
        av, bv = a.get(k), b.get(k)
        if av == bv:
            continue
        if av is None:
            out.append({"op": "add", "field": k, "new": bv})
        elif bv is None:
            out.append({"op": "del", "field": k, "old": av})
        else:
            out.append({"op": "chg", "field": k, "old": av, "new": bv})
        if len(out) >= limit:
            out.append({"op": "truncated"})
            break
    return out


def _diff_lines(a: list, b: list) -> list:
    import difflib
    out = []
    for line in difflib.unified_diff(a, b, "before", "after", lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        out.append(line)
    return out


def diff_model(a: dict, b: dict) -> dict:
    """Compare two canonical models; returns a change description."""
    if a["type"] != b["type"]:
        return {"type": "replaced", "from": a["type"], "to": b["type"]}
    t = a["type"]
    if t == "binary":
        if a["sha1"] == b["sha1"]:
            return {"type": "binary", "changed": False}
        return {"type": "binary", "changed": True, "old_bytes": a["bytes"], "new_bytes": b["bytes"],
                "old_sha1": a["sha1"], "new_sha1": b["sha1"]}
    if t == "version":
        changed = [k for k in ("type_tag", "build", "timestamp") if a.get(k) != b.get(k)]
        return {"type": "version", "changed": bool(changed), "fields": {k: [a.get(k), b.get(k)] for k in changed}}
    if t in ("text", "galaxy", "xml-unparsed"):
        lines = _diff_lines(a["lines"], b["lines"])
        return {"type": t, "changed": bool(lines), "lines": lines[:600],
                "added": sum(1 for l in lines if l.startswith("+")),
                "removed": sum(1 for l in lines if l.startswith("-"))}
    # keyed xml
    ea, eb = a["entities"], b["entities"]
    added = [k for k in eb if k not in ea]
    removed = [k for k in ea if k not in eb]
    changed = {}
    for k in ea:
        if k in eb:
            f = _diff_fields(ea[k], eb[k])
            if f:
                changed[k] = f
    root_attr_change = None
    if a.get("root_attrs") != b.get("root_attrs"):
        root_attr_change = [a.get("root_attrs"), b.get("root_attrs")]
    return {"type": "xml", "root": a["root"], "added": sorted(added), "removed": sorted(removed),
            "changed": changed, "root_attrs": root_attr_change,
            "changed_count": len(changed), "added_count": len(added), "removed_count": len(removed)}


def is_empty(d: dict) -> bool:
    t = d.get("type")
    if t == "binary":
        return not d.get("changed")
    if t == "version":
        return not d.get("changed")
    if t in ("text", "galaxy", "xml-unparsed"):
        return not d.get("changed")
    if t == "xml":
        return not (d["added"] or d["removed"] or d["changed"] or d["root_attrs"])
    return False


def describe(name: str, d: dict) -> str:
    """One-line summary for status output."""
    t = d.get("type")
    if is_empty(d):
        return "unchanged"
    if t == "binary":
        return "binary changed (%d -> %d bytes)" % (d["old_bytes"], d["new_bytes"])
    if t == "version":
        return "version sidecar changed: " + ", ".join("%s %s -> %s" % (k, v[0], v[1]) for k, v in d["fields"].items())
    if t in ("text", "galaxy", "xml-unparsed"):
        return "%s: +%d -%d lines" % (t, d.get("added", 0), d.get("removed", 0))
    if t == "xml":
        bits = []
        if d["added"]: bits.append("+%d entities" % d["added_count"])
        if d["removed"]: bits.append("-%d entities" % d["removed_count"])
        if d["changed"]: bits.append("~%d entities" % d["changed_count"])
        if d["root_attrs"]: bits.append("root attrs changed")
        return "xml(%s): %s" % (d["root"], ", ".join(bits))
    return "replaced"


def render(name: str, d: dict, max_fields: int = 8, max_entities: int = 25) -> str:
    """Human-readable, git-diff-like rendering of one component's change."""
    out = ["### %s" % name]
    t = d.get("type")
    if is_empty(d):
        return "\n".join(out + ["  (unchanged)"])
    if t == "binary":
        return "\n".join(out + ["  binary: %d -> %d bytes, sha1 %s -> %s"
                                % (d["old_bytes"], d["new_bytes"], d["old_sha1"], d["new_sha1"])])
    if t == "version":
        return "\n".join(out + ["  " + describe(name, d)])
    if t in ("text", "galaxy", "xml-unparsed"):
        return "\n".join(out + ["  " + describe(name, d)] + ["  " + l for l in d["lines"][:max_fields * 4]])
    if t == "xml":
        for k in d["removed"][:max_entities]:
            out.append("  - %s" % k)
        if len(d["removed"]) > max_entities:
            out.append("  - ... %d more removed" % (len(d["removed"]) - max_entities))
        for k in d["added"][:max_entities]:
            out.append("  + %s" % k)
        if len(d["added"]) > max_entities:
            out.append("  + ... %d more added" % (len(d["added"]) - max_entities))
        shown = 0
        for k, fields in sorted(d["changed"].items()):
            shown += 1
            if shown > max_entities:
                out.append("  ~ ... %d more changed entities" % (len(d["changed"]) - max_entities))
                break
            out.append("  ~ %s" % k)
            for f in fields[:max_fields]:
                if f["op"] == "add":
                    out.append("      + %s = %s" % (f["field"], f["new"]))
                elif f["op"] == "del":
                    out.append("      - %s = %s" % (f["field"], f["old"]))
                else:
                    out.append("      %s: %s -> %s" % (f["field"], f["old"], f["new"]))
            if len(fields) > max_fields:
                out.append("      ... %d more fields" % (len(fields) - max_fields))
        if d["root_attrs"]:
            out.append("  root attrs: %s -> %s" % tuple(d["root_attrs"]))
        return "\n".join(out)
    return "\n".join(out + ["  replaced: %s -> %s" % (d.get("from"), d.get("to"))])
