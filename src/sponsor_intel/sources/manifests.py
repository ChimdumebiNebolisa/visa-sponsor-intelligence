"""Atomic discovery and source-artifact manifest persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel

from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    RawArtifactManifestRecord,
    SourceArtifactCandidate,
)


def write_json_atomic(path: Path, model: BaseModel | dict[str, object]) -> None:
    """Write one JSON document atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
            json.dump(payload, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


class ArtifactManifestStore:
    """Deduplicated JSONL provenance manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> tuple[ArtifactManifestRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[ArtifactManifestRecord] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(ArtifactManifestRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(
                    f"Invalid source manifest record at {self.path}:{line_number}"
                ) from error
        return tuple(records)

    def latest_for_candidate(
        self, candidate: SourceArtifactCandidate
    ) -> ArtifactManifestRecord | None:
        matching = [
            record
            for record in self.records()
            if record.source_id == candidate.source_id
            and record.download_url == candidate.download_url
            and record.fiscal_year == candidate.fiscal_year
            and record.fiscal_quarter == candidate.fiscal_quarter
        ]
        if not matching:
            return None
        return max(matching, key=lambda record: record.retrieved_at)

    def upsert(self, record: ArtifactManifestRecord) -> None:
        by_id = {item.source_artifact_id: item for item in self.records()}
        by_id[record.source_artifact_id] = record
        ordered = sorted(
            by_id.values(),
            key=lambda item: (item.source_id, item.fiscal_year, item.file_name, item.sha256),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}-", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
                for item in ordered:
                    destination.write(item.model_dump_json())
                    destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise


class RawArtifactManifestStore:
    """Deduplicated raw-download provenance written before normalization."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> tuple[RawArtifactManifestRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[RawArtifactManifestRecord] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(RawArtifactManifestRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(
                    f"Invalid raw manifest record at {self.path}:{line_number}"
                ) from error
        return tuple(records)

    def latest_for_candidate(
        self, candidate: SourceArtifactCandidate
    ) -> RawArtifactManifestRecord | None:
        matching = [
            record
            for record in self.records()
            if record.source_id == candidate.source_id
            and record.download_url == candidate.download_url
            and record.fiscal_year == candidate.fiscal_year
            and record.fiscal_quarter == candidate.fiscal_quarter
        ]
        if not matching:
            return None
        return max(matching, key=lambda record: record.retrieved_at)

    def upsert(self, record: RawArtifactManifestRecord) -> None:
        by_id = {item.source_artifact_id: item for item in self.records()}
        by_id[record.source_artifact_id] = record
        ordered = sorted(
            by_id.values(),
            key=lambda item: (item.source_id, item.fiscal_year, item.file_name, item.sha256),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}-", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
                for item in ordered:
                    destination.write(item.model_dump_json())
                    destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
