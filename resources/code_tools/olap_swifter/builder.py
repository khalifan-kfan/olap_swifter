"""
Field Inventory.
Where every raw variable of a staged source goes, taken from the registered
schema's decisions: the table and role of each variable, or its written reason
for exclusion. It proves zero variable loss and feeds the review page, where a
person can switch a field between tables to explore the model.
"""

from typing import Any, Dict, List, Optional, Tuple

import duckdb


class FieldDefinition:
    def __init__(self, name: str, data_type: str, role: str, target_table: str, is_key: bool = False, description: str = ""):
        self.name = name
        self.data_type = data_type
        self.role = role  # 'dimension_attribute', 'metric', 'foreign_key', 'primary_key'
        self.target_table = target_table
        self.is_key = is_key
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "role": self.role,
            "target_table": self.target_table,
            "is_key": self.is_key,
            "description": self.description
        }


class DynamicOLAPBuilder:
    """The field inventory of one staged source, from its schema; no variable is left out."""

    def __init__(self, conn: Optional[duckdb.DuckDBPyConnection] = None):
        self.conn = conn or duckdb.connect(":memory:")
        self.fields: Dict[str, FieldDefinition] = {}
        self.raw_table_name: Optional[str] = None

    def load_mappings_from_schema(self, spec, resolution):
        """
        Field inventory from a registered schema's decisions for one staged source.
        `resolution` is a schema.materializer.Resolution.
        """
        roles = {"key": "primary_key", "attribute": "dimension_attribute", "measure": "metric"}
        self.fields.clear()
        self.raw_table_name = f"stg.{resolution.staging}"
        for col in spec.all_columns():
            raw_sql = resolution.raw_sql.get((col.table, col.name))
            if raw_sql is None:
                continue
            if col.crosswalk:
                raws = [col.name]
            elif col.expr:
                raws = [i for i in col.inputs]
            else:
                raws = [raw_sql.strip('"')]
            for raw in raws:
                if raw in self.fields:
                    continue
                self.fields[raw] = FieldDefinition(
                    name=raw, data_type=col.type, role=roles[col.role], target_table=col.table,
                    is_key=col.role == "key", description=col.description or f"{spec.schema_id}: {col.table}.{col.name}",
                )
        for raw, why in resolution.excluded_raw.items():
            self.fields[raw] = FieldDefinition(name=raw, data_type="VARCHAR", role="excluded",
                                               target_table="excluded", description=why)
        self.fields.pop("_source_row", None)

    def verify_no_variable_left_out_of(self, table: str) -> Tuple[bool, List[str]]:
        """Every raw column of a staged table has a place in the inventory (lineage columns ignored)."""
        raw_cols = set(self.conn.execute(f"PRAGMA table_info('{table}')").df()["name"]) - {"_source_row"}
        missing = sorted(raw_cols - set(self.fields))
        return (len(missing) == 0, missing)

    def switch_field(self, field_name: str, new_target_table: str, new_role: Optional[str] = None):
        """
        Switches a field to another table or role on the review page (a person
        exploring the model, or the Playwright check); the schema file is unchanged.
        """
        if field_name not in self.fields:
            raise KeyError(f"Field '{field_name}' not found in registered fields.")

        field = self.fields[field_name]
        field.target_table = new_target_table
        if new_role:
            field.role = new_role
            field.is_key = new_role in ["primary_key", "foreign_key"]

    def get_field_inventory(self) -> List[Dict[str, Any]]:
        """Returns the full inventory of all fields to guarantee zero variable loss."""
        return [f.to_dict() for f in self.fields.values()]
