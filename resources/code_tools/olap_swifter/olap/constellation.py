"""
Medical OLAP Fact Constellation (Galaxy) Schema — Metadata Only.
Tables and relationships come from the registered schema YAML; see
olap_swifter.schema.spec.SchemaSpec.metadata().
"""

from typing import Any, Dict, Optional

from olap_swifter.schema.registry import SchemaRegistry


def get_constellation_schema_metadata(schema_id: str = "clinical_visit",
                                      registry: Optional[SchemaRegistry] = None) -> Dict[str, Any]:
    """Relationships and join keys of a registered schema, in the shape JoinGraph expects."""
    return (registry or SchemaRegistry()).get(schema_id).metadata()
