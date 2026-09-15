"""
Schema Join Graph Analyzer.
Uses networkx to model the OLAP schema topology, calculating:
1. The exact number of joins required to link any metric and dimension.
2. The shortest join path across the constellation or snowflake.
3. Automatic generation of optimized multi-table JOIN clauses.
"""

from typing import Dict, List, Any, Optional, Tuple
import networkx as nx


class JoinGraph:
    """Graph representation of OLAP schema foreign key relationships."""

    def __init__(self):
        self.graph = nx.Graph()
        self.edge_keys: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def add_relationship(self, table_a: str, table_b: str, key_a: str, key_b: Optional[str] = None):
        """Adds a foreign key relationship edge between two tables."""
        key_b = key_b or key_a
        self.graph.add_edge(table_a, table_b, key_a=key_a, key_b=key_b)
        self.edge_keys[(table_a, table_b)] = (key_a, key_b)
        self.edge_keys[(table_b, table_a)] = (key_b, key_a)

    def load_from_metadata(self, meta: Dict[str, Any]):
        """Loads relationships from schema metadata dict."""
        for rel in meta.get("relationships", []):
            from_k = rel.get("from_key") or rel.get("key")
            to_k = rel.get("to_key") or rel.get("key")
            self.add_relationship(rel["from"], rel["to"], from_k, to_k)

    def get_number_of_joins(self, start_table: str, target_table: str) -> int:
        """Returns the minimum number of joins needed to connect start_table to target_table."""
        if start_table == target_table:
            return 0
        try:
            path = nx.shortest_path(self.graph, source=start_table, target=target_table)
            return len(path) - 1
        except nx.NetworkXNoPath:
            return -1  # No path exists

    def get_join_path(self, start_table: str, target_table: str) -> List[str]:
        """Returns the sequence of tables in the shortest join path."""
        if start_table == target_table:
            return [start_table]
        try:
            return nx.shortest_path(self.graph, source=start_table, target=target_table)
        except nx.NetworkXNoPath:
            return []

    def generate_join_sql(self, start_table: str, target_table: str) -> Tuple[str, int]:
        """
        Generates the explicit SQL JOIN clauses to connect start_table to target_table.
        Returns (join_sql_string, number_of_joins).
        """
        path = self.get_join_path(start_table, target_table)
        if not path or len(path) <= 1:
            return ("", 0)

        joins = []
        for i in range(len(path) - 1):
            curr_tbl = path[i]
            next_tbl = path[i + 1]
            keys = self.edge_keys.get((curr_tbl, next_tbl))
            if keys:
                from_k, to_k = keys
                joins.append(f"JOIN {next_tbl} ON {curr_tbl}.{from_k} = {next_tbl}.{to_k}")
            else:
                joins.append(f"JOIN {next_tbl} ON 1=1")

        return ("\n".join(joins), len(path) - 1)

    def analyze_all_paths_from_fact(self, fact_table: str) -> Dict[str, Any]:
        """Analyzes join distance and complexity from a given fact table to all reachable tables."""
        analysis = {}
        for node in self.graph.nodes:
            if node != fact_table:
                distance = self.get_number_of_joins(fact_table, node)
                path = self.get_join_path(fact_table, node)
                analysis[node] = {
                    "number_of_joins": distance,
                    "path": path,
                    "is_direct_join": (distance == 1)
                }
        return analysis
