"""Derived analytics for traces, events, quantities, and spans."""

from tracker.analytics.agent import build_agent_summary
from tracker.analytics.cache import build_cache_summary
from tracker.analytics.coverage import build_coverage_exactness, build_coverage_exactness_from_events
from tracker.analytics.latency import build_latency_summary
from tracker.analytics.observation_contract import build_observation_contract_summary
from tracker.analytics.provider_validation import (
    build_provider_validation_matrix,
    summarize_provider_validation,
)
from tracker.analytics.rag import build_rag_summary
from tracker.analytics.reliability import build_reliability_summary
from tracker.analytics.service_attribution import build_service_attribution
from tracker.analytics.trust_report import TrustReport, build_trust_report, build_trust_report_from_events

__all__ = [
    "build_agent_summary",
    "build_cache_summary",
    "build_coverage_exactness",
    "build_coverage_exactness_from_events",
    "build_latency_summary",
    "build_observation_contract_summary",
    "build_provider_validation_matrix",
    "summarize_provider_validation",
    "build_reliability_summary",
    "build_rag_summary",
    "build_service_attribution",
    "TrustReport",
    "build_trust_report",
    "build_trust_report_from_events",
]
