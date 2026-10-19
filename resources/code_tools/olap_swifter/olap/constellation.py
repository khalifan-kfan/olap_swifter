"""
Medical OLAP Fact Constellation (Galaxy) Schema — Metadata Only.
Tables and relationships come from the registered schema YAML; see
olap_swifter.schema.spec.SchemaSpec.metadata().
"""

from typing import Any, Dict, Optional

from olap_swifter.schema.registry import SchemaRegistry


def get_constellation_schema_metadata(schema_id: Optional[str] = None,
                                      registry: Optional[SchemaRegistry] = None) -> Dict[str, Any]:
    """Relationships and join keys of a registered schema (default: the only one), in the shape JoinGraph expects."""
    registry = registry or SchemaRegistry()
    return registry.get(registry.default_id(schema_id)).metadata()
