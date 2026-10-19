from __future__ import annotations
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, model_validator

FILE_METADATA = {"source_system", "business_date", "version"}

class Column(BaseModel):
    name: str
    type: Literal["string", "integer", "date", "timestamp"]
    nullable: bool = False
    allowed: list[str] | None = None
    min: int | None = None
    description: str | None = None

class Rule(BaseModel):
    id: str
    scope: Literal["row", "file"] = "row"
    description: str

class Contract(BaseModel):
    name: str
    version: str
    owner: str
    source_system: str
    delivery: dict
    format: dict
    identity: dict[str, list[str]]
    columns: list[Column]
    rules: list[Rule] = []

    @model_validator(mode="after")
    def keys_reference_real_columns(self) -> "Contract":
        cols = {c.name for c in self.columns}
        if len(cols) != len(self.columns):
            raise ValueError(f"{self.name}: duplicate column names")
        for k in self.identity["record_key"]:
            if k not in cols:
                raise ValueError(f"{self.name}: record_key '{k}' is not a column")
        for k in self.identity["file_key"]:
            if k not in cols | FILE_METADATA:
                raise ValueError(f"{self.name}: file_key '{k}' is neither a column nor file metadata")
        return self

def load(path: Path) -> Contract:
    raw = yaml.safe_load(path.read_text())
    if not raw:
        raise ValueError(f"{path.name} is empty")
    body = {k: raw[k] for k in ("delivery", "format", "identity", "columns")}
    return Contract(**raw["contract"], **body, rules=raw.get("rules", []))