"""Typed source-registry loading."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from sponsor_intel.sources.models import SourceConfig

DEFAULT_SOURCE_REGISTRY_PATH = Path("configs/sources.yaml")


class _RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[SourceConfig, ...]


class SourceRegistry:
    """Validated, duplicate-free source configurations."""

    def __init__(self, sources: tuple[SourceConfig, ...]) -> None:
        by_id = {source.id: source for source in sources}
        if len(by_id) != len(sources):
            raise ValueError("Source IDs must be unique")
        self._sources = by_id

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_SOURCE_REGISTRY_PATH) -> SourceRegistry:
        if not path.is_file():
            raise ValueError(f"Source registry is missing: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        document = _RegistryDocument.model_validate(loaded)
        return cls(document.sources)

    def list(self) -> tuple[SourceConfig, ...]:
        return tuple(self._sources[source_id] for source_id in sorted(self._sources))

    def get(self, source_id: str) -> SourceConfig:
        try:
            return self._sources[source_id]
        except KeyError as error:
            available = ", ".join(sorted(self._sources))
            raise ValueError(
                f"Unknown source '{source_id}'. Available sources: {available}"
            ) from error
