from pathlib import Path

import polars as pl

from sponsor_intel.role_classification.pipeline import RoleClassificationPipeline


def _write_source(data_root: Path, source_id: str, fiscal_year: int, frame: pl.DataFrame) -> None:
    path = (
        data_root
        / "resolved"
        / "sources"
        / source_id
        / f"fy={fiscal_year}"
        / f"{source_id}-{fiscal_year}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def test_pipeline_classifies_every_dol_row_and_preserves_source_values(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    _write_source(
        data_root,
        "dol_lca",
        2026,
        pl.DataFrame(
            {
                "case_id": ["LCA-1", "LCA-2", "LCA-3"],
                "source_id": ["dol_lca"] * 3,
                "fiscal_year": [2026] * 3,
                "job_title_raw": ["Software Engineer", "Research Scientist", "Physician"],
                "soc_code": ["15-1252.00", None, "29-1215.00"],
            }
        ),
    )
    _write_source(
        data_root,
        "dol_perm",
        2025,
        pl.DataFrame(
            {
                "case_id": ["PERM-1", "PERM-2"],
                "source_id": ["dol_perm"] * 2,
                "fiscal_year": [2025] * 2,
                "job_title_raw": ["Data Engineer", "Crew Member"],
                "soc_code": [None, "35-3023.00"],
            }
        ),
    )
    pipeline = RoleClassificationPipeline(data_root=data_root, output_root=output_root)

    summary = pipeline.build()

    assert summary.record_count == 5
    assert summary.technical_record_count == 2
    assert summary.ambiguous_record_count == 1
    assert summary.review_queue_count == 1
    assert summary.classifications_path.is_file()
    assert summary.review_queue_path.is_file()
    classified_path = next((data_root / "classified" / "sources" / "dol_lca").rglob("*.parquet"))
    classified = pl.read_parquet(classified_path)
    assert classified["job_title_raw"].to_list() == [
        "Software Engineer",
        "Research Scientist",
        "Physician",
    ]
    assert classified["role_family"].to_list() == [
        "software_engineering",
        "ambiguous",
        "not_relevant",
    ]
    assert classified["classification_version"].n_unique() == 1
    assert {
        "technical_role",
        "role_family",
        "role_confidence",
        "classification_method",
        "classification_version",
        "review_status",
    }.issubset(classified.columns)
    assert "role_review_status" not in classified.columns
