"""Typed contracts for policy ranking, documents, extraction, and review."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FactType(StrEnum):
    """Policy questions required by SPEC.md section 20.7."""

    H1B_FACULTY_ELIGIBLE = "h1b_faculty_eligible"
    H1B_RESEARCH_STAFF_ELIGIBLE = "h1b_research_staff_eligible"
    H1B_GENERAL_STAFF_ELIGIBLE = "h1b_general_staff_eligible"
    H1B_POSTDOC_ELIGIBLE = "h1b_postdoc_eligible"
    PR_FACULTY_ELIGIBLE = "pr_faculty_eligible"
    PR_RESEARCH_STAFF_ELIGIBLE = "pr_research_staff_eligible"
    PR_GENERAL_STAFF_ELIGIBLE = "pr_general_staff_eligible"
    PR_POSTDOC_ELIGIBLE = "pr_postdoc_eligible"
    PERM_SUPPORTED = "perm_supported"
    EB1B_SUPPORTED = "eb1b_supported"
    NIW_EMPLOYER_SUPPORTED_OR_ASSISTED = "niw_employer_supported_or_assisted"
    TEMPORARY_POSITIONS_EXCLUDED = "temporary_positions_excluded"
    GRANT_FUNDED_POSITIONS_EXCLUDED = "grant_funded_positions_excluded"
    MINIMUM_APPOINTMENT_DURATION = "minimum_appointment_duration"
    MINIMUM_FUNDING_DURATION = "minimum_funding_duration"
    WAITING_PERIOD = "waiting_period"
    REQUIRED_APPROVAL_LEVEL = "required_approval_level"
    DEPARTMENT_INITIATES = "department_initiates"
    EMPLOYEE_SELF_INITIATION_ALLOWED = "employee_self_initiation_allowed"
    COST_PAYMENT_POLICY = "cost_payment_policy"
    CAP_EXEMPTION_EXPLICITLY_STATED = "cap_exemption_explicitly_stated"
    POLICY_DISCRETIONARY = "policy_discretionary"
    POLICY_LAST_UPDATED = "policy_last_updated"


REQUIRED_FACT_TYPES = tuple(FactType)


class FactValue(StrEnum):
    """Conservative values allowed by the extraction contract."""

    YES = "YES"
    NO = "NO"
    LIMITED = "LIMITED"
    UNKNOWN = "UNKNOWN"
    NOT_STATED = "NOT_STATED"


class ParseStatus(StrEnum):
    """Document parsing outcomes."""

    PARSED = "PARSED"
    EMPTY = "EMPTY"
    UNSUPPORTED = "UNSUPPORTED"
    SUSPICIOUS = "SUSPICIOUS"
    FAILED = "FAILED"


class ReviewStatus(StrEnum):
    """Human-review states; only accepted rows enter product signals."""

    NEEDS_REVIEW = "NEEDS_REVIEW"
    REVIEWED_ACCEPTED = "REVIEWED_ACCEPTED"
    REVIEWED_NOT_STATED = "REVIEWED_NOT_STATED"
    REVIEWED_REJECTED = "REVIEWED_REJECTED"


class PolicyCandidate(BaseModel):
    """One institution selected for bounded policy enrichment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_rank: int = Field(ge=1)
    institution_id: str
    official_name: str
    official_domain: str
    system_name: str | None
    organization_id: str | None
    state: str | None
    control: str | None
    candidate_score: float = Field(ge=0, le=1)
    relevant_lca_component: float = Field(ge=0, le=1)
    relevant_perm_component: float = Field(ge=0, le=1)
    recent_activity_component: float = Field(ge=0, le=1)
    total_rd_component: float = Field(ge=0, le=1)
    computing_rd_component: float = Field(ge=0, le=1)
    engineering_rd_component: float = Field(ge=0, le=1)
    opt_component: float = Field(ge=0, le=1)
    everify_component: float = Field(ge=0, le=1)
    institution_type_component: float = Field(ge=0, le=1)
    manual_priority_component: float = Field(ge=0, le=1)


class DiscoveredPolicyDocument(BaseModel):
    """Structured official-domain discovery result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    title: str
    document_type: str
    relevance_reason: str


class PolicyDiscoveryResult(BaseModel):
    """Strict response returned by bounded OpenAI web discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    documents: list[DiscoveredPolicyDocument]


class ExtractedPolicyFact(BaseModel):
    """One source-grounded answer returned by Structured Outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_type: FactType
    value: FactValue
    qualifier: str | None
    supporting_excerpt: str
    section_or_page: str | None
    source_url: str
    confidence: float = Field(ge=0, le=1)


class PolicyExtraction(BaseModel):
    """Strict institution-policy extraction schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    institution_name: str
    facts: list[ExtractedPolicyFact]
    document_summary: str
    contradictions: list[str]
    needs_human_review: bool

    @model_validator(mode="after")
    def require_every_fact_type_once(self) -> PolicyExtraction:
        actual = [fact.fact_type for fact in self.facts]
        if len(actual) != len(REQUIRED_FACT_TYPES) or set(actual) != set(REQUIRED_FACT_TYPES):
            raise ValueError("Extraction must contain every required fact type exactly once")
        return self


class PolicyDocument(BaseModel):
    """Immutable retrieved policy artifact and parsed-text provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_document_id: str
    institution_id: str
    document_type: str
    title: str
    url: str
    official_domain: str
    retrieved_at: datetime
    http_status: int
    content_type: str
    content_sha256: str
    text_sha256: str
    published_or_updated_date: str | None
    raw_path: Path
    parsed_text_path: Path
    is_current: bool
    parse_status: ParseStatus
    discovery_method: str
    suspicious_text: bool
    cache_hit: bool


class CachedExtraction(BaseModel):
    """No-charge replay record keyed by document content and extractor version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_key: str
    text_sha256: str
    extractor_version: str
    model_name: str
    model_response_id: str
    extracted_at: datetime
    extraction: PolicyExtraction


class PolicyFact(BaseModel):
    """Persisted fact plus exact-evidence and human-review state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_fact_id: str
    institution_id: str
    policy_document_id: str
    fact_type: FactType
    fact_value: FactValue
    qualifier: str | None
    supporting_excerpt: str
    section_or_page: str | None
    source_url: str
    retrieved_at: datetime
    extractor_version: str
    model_name: str
    model_response_id: str
    confidence: float = Field(ge=0, le=1)
    exact_excerpt_verified: bool
    human_review_status: ReviewStatus
    reviewer_note: str | None
    contradiction_group_id: str | None
    valid_from: datetime
    valid_to: datetime | None
    is_current: bool


class PolicyBuildSummary(BaseModel):
    """Stable audit metadata returned by a Phase 7 build."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int = Field(ge=0)
    discovered_document_count: int = Field(ge=0)
    parsed_document_count: int = Field(ge=0)
    extracted_document_count: int = Field(ge=0)
    extraction_cache_hit_count: int = Field(ge=0)
    api_call_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    accepted_fact_count: int = Field(ge=0)
    reviewed_institution_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    candidates_path: Path
    documents_path: Path
    facts_path: Path
    review_queue_path: Path
    errors_path: Path
    summary_path: Path


class PolicyBenchmarkAnnotation(BaseModel):
    """One manually reviewed expected fact for extraction evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    institution_id: str
    official_name: str
    source_url: str
    fact_type: FactType
    expected_value: FactValue
    supporting_excerpt: str
    reviewer_note: str


class PolicyEvaluationResult(BaseModel):
    """Measured Phase 7 acceptance gates over the manual benchmark."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_institution_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    evaluated_prediction_count: int = Field(ge=0)
    correct_prediction_count: int = Field(ge=0)
    factual_precision: float = Field(ge=0, le=1)
    benchmark_coverage: float = Field(ge=0, le=1)
    accepted_official_url_rate: float = Field(ge=0, le=1)
    accepted_excerpt_rate: float = Field(ge=0, le=1)
    unsupported_accepted_fact_count: int = Field(ge=0)
    passed: bool
    report_path: Path
