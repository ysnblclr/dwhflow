"""İki model arasındaki fark: onay öncesi önizleme (US-3.7) ve çalıştırma karşılaştırması (US-7.4)."""

from __future__ import annotations

from typing import Any

from ..model import Model
from ..render.slices import dataset_links

_FIELDS = ("name", "layer", "kind", "system", "status", "description")


def _value(node: Any, field: str) -> Any:
    value = getattr(node, field, None)
    return getattr(value, "value", value)


def diff_models(before: Model, after: Model, *, limit: int = 500) -> dict[str, Any]:
    """Nesne ve tablo düzeyi bağ farkı. Nesneler kimlikle eşleşir (kimlikler addan türer)."""
    old_nodes, new_nodes = before.nodes, after.nodes
    added = [_node_row(new_nodes[i]) for i in sorted(set(new_nodes) - set(old_nodes))]
    removed = [_node_row(old_nodes[i]) for i in sorted(set(old_nodes) - set(new_nodes))]
    changed = []
    for node_id in sorted(set(old_nodes) & set(new_nodes)):
        old, new = old_nodes[node_id], new_nodes[node_id]
        for field in _FIELDS:
            if _value(old, field) != _value(new, field):
                changed.append({"id": node_id, "name": new.name, "field": field,
                                "old": _value(old, field), "new": _value(new, field)})
        old_cols = {c.name for c in getattr(old, "columns", [])}
        new_cols = {c.name for c in getattr(new, "columns", [])}
        if old_cols != new_cols:
            changed.append({"id": node_id, "name": new.name, "field": "columns",
                            "old": sorted(old_cols - new_cols), "new": sorted(new_cols - old_cols)})

    def names(model: Model) -> dict[str, str]:
        return {node_id: node.name for node_id, node in model.nodes.items()}

    old_links, new_links = dataset_links(before), dataset_links(after)
    old_names, new_names = names(before), names(after)

    def link_row(key, link, names_):
        return {"source": key[0], "target": key[1], "source_name": names_.get(key[0], key[0]),
                "target_name": names_.get(key[1], key[1]), "draft": link.draft,
                "processes": [names_.get(p, p) for p in link.processes]}

    links_added = [link_row(k, new_links[k], new_names) for k in sorted(set(new_links) - set(old_links))]
    links_removed = [link_row(k, old_links[k], old_names) for k in sorted(set(old_links) - set(new_links))]
    links_changed = []
    for key in sorted(set(old_links) & set(new_links)):
        old, new = old_links[key], new_links[key]
        if old.draft != new.draft or sorted(old.processes) != sorted(new.processes):
            row = link_row(key, new, new_names)
            row["was_draft"] = old.draft
            row["old_processes"] = [old_names.get(p, p) for p in old.processes]
            links_changed.append(row)

    old_edges = {e.id for e in before.edges}
    new_edges = {e.id for e in after.edges}
    result = {
        "nodes_added": added[:limit], "nodes_removed": removed[:limit], "nodes_changed": changed[:limit],
        "links_added": links_added[:limit], "links_removed": links_removed[:limit],
        "links_changed": links_changed[:limit],
        "edges_added": len(new_edges - old_edges), "edges_removed": len(old_edges - new_edges),
    }
    result["empty"] = not any(result[k] for k in result if k != "empty")
    return result


def _node_row(node: Any) -> dict[str, Any]:
    return {"id": node.id, "name": node.name, "type": node.node_type.value,
            "layer": node.layer.value, "kind": node.kind.value}
