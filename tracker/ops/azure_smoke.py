"""Live Azure OpenAI smoke harness with audit artifacts.

The smoke suite is intentionally tiny-cost by default. The optional demo suite exercises
several realistic workloads across every configured surface. Both capture raw responses,
normalize TokenEvents, and write audit bundles that can be reviewed without re-running live
traffic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urlerr
from urllib import parse, request

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter
from tracker.analytics.trust_report import build_trust_report
from tracker.context.model import TraceContext, new_trace
from tracker.export.csv_exporter import export_csv
from tracker.export.excel_exporter import export_excel
from tracker.models.enums import PrecisionLevel
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.normalization.normalizer import normalize
from tracker.observability.observation import Observation
from tracker.ops.auth_token import load_auth_token
from tracker.ops.provider_proof import write_capture_attestation
from tracker.ops.runtime_fingerprint import runtime_fingerprint
from tracker.storage.file_repository import FileRepository
from tracker.streaming.sse import parse_sse_json
from tracker.streaming.stream_consumer import consume_stream

Opener = Callable[[request.Request, float], Any]
_SURFACE_NAMES = frozenset({"chat", "responses", "embeddings"})
_SUITE_NAMES = frozenset({"smoke", "demo"})


@dataclass(frozen=True)
class AzureSmokeCase:
    """One live Azure call to attempt."""

    name: str
    profile: str
    surface: str
    deployment: str
    endpoint: str
    body: dict[str, Any]
    api_version: str | None = None
    service_name: str = "azure-smoke"
    use_case: str | None = None
    expectation: dict[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None
    conversation_step: int | None = None
    previous_case: str | None = None
    stream: bool = False


@dataclass(frozen=True)
class AzureSmokeResult:
    """One smoke case outcome."""

    case: str
    surface: str
    status: str
    detail: str
    http_status: int | None = None
    event_id: str | None = None
    contributing_tokens: int = 0
    provider_total_tokens: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    artifact: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def skipped(self) -> bool:
        return self.status == "skip"


@dataclass(frozen=True)
class AzureSmokeSummary:
    """Whole-run result."""

    out_dir: str
    passed: bool
    ran_count: int
    skipped_count: int
    failure_count: int
    event_count: int
    observed_total_contributing_tokens: int
    artifacts: dict[str, str]
    results: list[AzureSmokeResult]
    suite: str = "smoke"
    trace_id: str | None = None
    collector_url: str | None = None
    collector_status: str = "not_requested"
    collector_detail: str | None = None
    collector_persisted_event_count: int = 0
    collector_trace_tokens: int | None = None
    collector_total_before: int | None = None
    collector_total_after: int | None = None
    generated_at: str = ""
    runtime_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results"] = [asdict(result) for result in self.results]
        return data


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_timestamp_for_path() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _env(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resource_endpoint(endpoint: str) -> str:
    """Return the Azure OpenAI resource endpoint used by deployment routes."""
    trimmed = endpoint.rstrip("/")
    suffix = "/openai/v1"
    if trimmed.lower().endswith(suffix):
        return trimmed[: -len(suffix)]
    return trimmed


def _responses_endpoint(endpoint: str) -> str:
    """Return the Azure OpenAI v1 endpoint used by the Responses route."""
    trimmed = endpoint.rstrip("/")
    if trimmed.lower().endswith("/openai/v1"):
        return trimmed
    return f"{trimmed}/openai/v1"


def _deployment_error(value: str | None) -> str | None:
    if not value:
        return None
    if any(char.isspace() for char in value) or ">" in value or "<" in value:
        return "deployment name contains whitespace or shell prompt markers"
    return None


def _api_key_format_error(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return "credential_format_invalid: API key looks like an endpoint URL"
    if ";" in value:
        return "credential_format_invalid: API key looks like a connection string"
    return None


def _missing(environment: Mapping[str, str], keys: Sequence[str]) -> list[str]:
    return [key for key in keys if not _env(environment, key)]


def _redacted_config(environment: Mapping[str, str]) -> dict[str, Any]:
    keys = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_RESPONSES_ENDPOINT",
        "AZURE_OPENAI_RESPONSES_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",
        "AZURE_REGION",
    )
    data = {key: _env(environment, key) for key in keys if _env(environment, key)}
    data["AZURE_OPENAI_API_KEY"] = "present" if _env(environment, "AZURE_OPENAI_API_KEY") else "missing"
    data["configured_profiles"] = _configured_profiles(environment)
    return data


def _configured_profiles(environment: Mapping[str, str]) -> list[str]:
    profiles: list[str] = []
    if not _missing(environment, ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_RESPONSES_ENDPOINT", "AZURE_OPENAI_RESPONSES_DEPLOYMENT")):
        profiles.append("foundry-responses")
    if not _missing(environment, ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")):
        profiles.append("azure-chat")
    if not _missing(environment, ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")):
        profiles.append("azure-embeddings")
    return profiles


def _planned_smoke_cases(environment: Mapping[str, str]) -> tuple[list[AzureSmokeCase], list[AzureSmokeResult]]:
    """Build runnable cases from env vars and skip records for missing optional surfaces."""
    api_key_missing = _missing(environment, ("AZURE_OPENAI_API_KEY",))
    api_key_error = _api_key_format_error(_env(environment, "AZURE_OPENAI_API_KEY"))
    skips: list[AzureSmokeResult] = []
    cases: list[AzureSmokeCase] = []
    api_version = _env(environment, "AZURE_OPENAI_API_VERSION") or "2024-10-21"

    chat_missing = api_key_missing + _missing(environment, ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"))
    if chat_missing:
        skips.append(_skip("chat", "chat_completions", chat_missing))
    elif api_key_error:
        skips.append(AzureSmokeResult("chat", "chat_completions", "skip", api_key_error))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_DEPLOYMENT")):
        skips.append(AzureSmokeResult("chat", "chat_completions", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="chat",
                profile="azure-chat",
                surface="chat_completions",
                endpoint=_resource_endpoint(_env(environment, "AZURE_OPENAI_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_DEPLOYMENT") or "",
                api_version=api_version,
                body={
                    "messages": [{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
        )

    responses_missing = api_key_missing + _missing(
        environment,
        ("AZURE_OPENAI_RESPONSES_ENDPOINT", "AZURE_OPENAI_RESPONSES_DEPLOYMENT"),
    )
    if responses_missing:
        skips.append(_skip("responses", "responses", responses_missing))
    elif api_key_error:
        skips.append(AzureSmokeResult("responses", "responses", "skip", api_key_error))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT")):
        skips.append(AzureSmokeResult("responses", "responses", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="responses",
                profile="foundry-responses",
                surface="responses",
                endpoint=_responses_endpoint(_env(environment, "AZURE_OPENAI_RESPONSES_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT") or "",
                body={
                    "model": _env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT"),
                    "input": "Reponds en un seul mot: bonjour",
                    "max_output_tokens": 128,
                    "reasoning": {"effort": "low"},
                },
            )
        )

    embeddings_missing = api_key_missing + _missing(
        environment,
        ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"),
    )
    if embeddings_missing:
        skips.append(_skip("embeddings", "embeddings", embeddings_missing))
    elif api_key_error:
        skips.append(AzureSmokeResult("embeddings", "embeddings", "skip", api_key_error))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")):
        skips.append(AzureSmokeResult("embeddings", "embeddings", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="embeddings",
                profile="azure-embeddings",
                surface="embeddings",
                endpoint=_resource_endpoint(_env(environment, "AZURE_OPENAI_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT") or "",
                api_version=api_version,
                body={"input": "bonjour"},
            )
        )
    return cases, skips


def _responses_demo_cases(base: AzureSmokeCase) -> list[AzureSmokeCase]:
    common = {
        "model": base.deployment,
        "max_output_tokens": 256,
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "low"},
    }
    return [
        AzureSmokeCase(
            name="responses-rag-grounding",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Contexte certifie: INC-204 concerne le service Paiements. L'incident est SEV-2, "
                    "le proprietaire est l'equipe FinOps, et l'action approuvee est de desactiver le "
                    "nouveau routage puis verifier le taux d'erreur. Reponds uniquement avec un objet "
                    "JSON fonde sur ce contexte; n'invente aucune information."
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "grounded_incident_answer",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "incident_id": {"type": "string"},
                                "severity": {"type": "string"},
                                "owner": {"type": "string"},
                                "next_action": {"type": "string"},
                            },
                            "required": ["incident_id", "severity", "owner", "next_action"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            service_name="azure-demo-rag",
            use_case="grounded_incident_answer",
            expectation={
                "kind": "json",
                "required_keys": ["incident_id", "severity", "owner", "next_action"],
            },
        ),
        AzureSmokeCase(
            name="responses-agent-routing",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Le service Paiements renvoie 18% d'erreurs HTTP 503 apres un changement de routage. "
                    "Selectionne le runbook adapte en appelant exactement l'outil lookup_runbook."
                ),
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup_runbook",
                        "description": "Recherche le runbook operationnel d'un service et d'un symptome.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "service": {"type": "string"},
                                "symptom": {"type": "string"},
                                "severity": {"type": "string", "enum": ["SEV-1", "SEV-2", "SEV-3"]},
                            },
                            "required": ["service", "symptom", "severity"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    }
                ],
                "tool_choice": {"type": "function", "name": "lookup_runbook"},
            },
            service_name="azure-demo-agent",
            use_case="agent_tool_routing",
            expectation={"kind": "function_call", "tool_name": "lookup_runbook"},
        ),
        AzureSmokeCase(
            name="responses-risk-extraction",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Analyse ce journal fictif: 'user_id=U-104 action=export dataset=customer_contacts "
                    "rows=42000 destination=personal-drive approval=false'. Classe le risque et retourne "
                    "uniquement le JSON demande. recommended_control doit etre une action concrete de "
                    "12 mots maximum."
                ),
                "max_output_tokens": 512,
                "text": {
                    "verbosity": "low",
                    "format": {
                        "type": "json_schema",
                        "name": "risk_assessment",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                                "policy_violation": {"type": "boolean"},
                                "recommended_control": {"type": "string"},
                            },
                            "required": ["risk_level", "policy_violation", "recommended_control"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            service_name="azure-demo-governance",
            use_case="structured_risk_extraction",
            expectation={
                "kind": "json",
                "required_keys": ["risk_level", "policy_violation", "recommended_control"],
            },
        ),
        AzureSmokeCase(
            name="responses-stream-capacity-decision",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Un service traite 120 requetes par seconde. Une instance en absorbe 35 a 70% "
                    "d'utilisation maximale. On exige 25% de marge apres la perte d'une instance. "
                    "Determine le nombre minimal d'instances et explique le calcul en quatre phrases maximum."
                ),
                "stream": True,
            },
            service_name="azure-demo-capacity",
            use_case="streamed_capacity_reasoning",
            expectation={"kind": "text"},
            stream=True,
        ),
    ]


def _math_demo_cases(base: AzureSmokeCase) -> list[AzureSmokeCase]:
    common = {
        "model": base.deployment,
        "max_output_tokens": 1024,
        "reasoning": {"effort": "low"},
    }
    conversation_id = "factory-optimization-session"
    return [
        AzureSmokeCase(
            name="responses-math-optimization",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Une usine fabrique des produits A et B en quantites entieres x et y. "
                    "Maximiser 40x + 30y sous 2x + y <= 40, x + 2y <= 50, x >= 0, y >= 0. "
                    "Calcule la solution optimale et justifie-la brievement. Retourne uniquement le JSON demande."
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "integer_optimization_solution",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "max_profit": {"type": "integer"},
                                "optimal": {"type": "boolean"},
                                "explanation": {"type": "string"},
                            },
                            "required": ["x", "y", "max_profit", "optimal", "explanation"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            service_name="azure-demo-math",
            use_case="integer_optimization",
            expectation={
                "kind": "json",
                "required_keys": ["x", "y", "max_profit", "optimal", "explanation"],
                "expected_values": {"x": 10, "y": 20, "max_profit": 1000, "optimal": True},
            },
            conversation_id=conversation_id,
            conversation_step=1,
        ),
        AzureSmokeCase(
            name="responses-math-followup-verification",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Verifie maintenant numeriquement ta solution precedente: utilisation de chaque ressource, "
                    "profit obtenu, faisabilite et optimalite. Retourne uniquement le JSON demande."
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "optimization_verification",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "feasible": {"type": "boolean"},
                                "resource_1_used": {"type": "integer"},
                                "resource_2_used": {"type": "integer"},
                                "profit": {"type": "integer"},
                                "optimal": {"type": "boolean"},
                            },
                            "required": ["feasible", "resource_1_used", "resource_2_used", "profit", "optimal"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            service_name="azure-demo-math",
            use_case="optimization_verification",
            expectation={
                "kind": "json",
                "required_keys": ["feasible", "resource_1_used", "resource_2_used", "profit", "optimal"],
                "expected_values": {
                    "feasible": True,
                    "resource_1_used": 40,
                    "resource_2_used": 50,
                    "profit": 1000,
                    "optimal": True,
                },
            },
            conversation_id=conversation_id,
            conversation_step=2,
            previous_case="responses-math-optimization",
        ),
        AzureSmokeCase(
            name="responses-math-followup-sensitivity",
            profile=base.profile,
            surface=base.surface,
            endpoint=base.endpoint,
            deployment=base.deployment,
            body={
                **common,
                "input": (
                    "Deuxieme suivi: le profit unitaire de A passe de 40 a 70 tandis que toutes les autres "
                    "donnees du probleme precedent restent identiques. Recalcule l'optimum et indique s'il a "
                    "change. Retourne uniquement le JSON demande."
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "optimization_sensitivity",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "max_profit": {"type": "integer"},
                                "changed_optimum": {"type": "boolean"},
                            },
                            "required": ["x", "y", "max_profit", "changed_optimum"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            service_name="azure-demo-math",
            use_case="optimization_sensitivity",
            expectation={
                "kind": "json",
                "required_keys": ["x", "y", "max_profit", "changed_optimum"],
                "expected_values": {"x": 20, "y": 0, "max_profit": 1400, "changed_optimum": True},
            },
            conversation_id=conversation_id,
            conversation_step=3,
            previous_case="responses-math-followup-verification",
        ),
    ]


def _demo_chat_case(base: AzureSmokeCase) -> AzureSmokeCase:
    body: dict[str, Any] = {
        "messages": [
            {
                "role": "system",
                "content": "Tu es un SRE. Produis une synthese factuelle, concise et directement actionnable.",
            },
            {
                "role": "user",
                "content": (
                    "A 09:12 le taux d'erreur Paiements passe de 0,4% a 18%. A 09:18 le nouveau routage "
                    "est desactive. A 09:24 le taux revient a 0,6%. Donne impact, cause probable et action suivante."
                ),
            },
        ],
    }
    if base.profile == "foundry-chat-v1":
        body.update(
            {
                "model": base.deployment,
                "max_completion_tokens": 2048,
                "reasoning_effort": "minimal",
                "verbosity": "low",
            }
        )
    else:
        body.update({"max_tokens": 128, "temperature": 0})
    return AzureSmokeCase(
        name="chat-incident-summary",
        profile=base.profile,
        surface=base.surface,
        endpoint=base.endpoint,
        deployment=base.deployment,
        api_version=base.api_version,
        body=body,
        service_name="azure-demo-operations",
        use_case="incident_communication",
        expectation={"kind": "text"},
    )


def _demo_embedding_case(base: AzureSmokeCase) -> AzureSmokeCase:
    body: dict[str, Any] = {
        "input": [
            "Runbook Paiements: desactiver le routage recent si le taux HTTP 503 depasse 5%.",
            "Runbook Identite: verifier la rotation des certificats avant de relancer les pods.",
            "Runbook Recherche: reconstruire l'index seulement apres validation de la source documentaire.",
        ]
    }
    if base.profile == "foundry-embeddings-v1":
        body["model"] = base.deployment
    return AzureSmokeCase(
        name="embeddings-rag-batch",
        profile=base.profile,
        surface=base.surface,
        endpoint=base.endpoint,
        deployment=base.deployment,
        api_version=base.api_version,
        body=body,
        service_name="azure-demo-embeddings",
        use_case="rag_indexing",
        expectation={"kind": "embeddings", "count": 3},
    )


def _planned_demo_cases(environment: Mapping[str, str]) -> tuple[list[AzureSmokeCase], list[AzureSmokeResult]]:
    smoke_cases, smoke_skips = _planned_smoke_cases(environment)
    by_surface = {case.surface: case for case in smoke_cases}
    skips_by_surface = {result.surface: result for result in smoke_skips}
    cases: list[AzureSmokeCase] = []
    skips: list[AzureSmokeResult] = []

    responses = by_surface.get("responses")
    if responses:
        cases.extend(_responses_demo_cases(responses))
        cases.extend(_math_demo_cases(responses))
    elif skipped := skips_by_surface.get("responses"):
        skips.append(skipped)

    chat = by_surface.get("chat_completions")
    if not chat and responses:
        chat = AzureSmokeCase(
            name="chat",
            profile="foundry-chat-v1",
            surface="chat_completions",
            endpoint=responses.endpoint,
            deployment=responses.deployment,
            body={},
        )
    if chat:
        cases.append(_demo_chat_case(chat))
    elif skipped := skips_by_surface.get("chat_completions"):
        skips.append(skipped)

    embeddings = by_surface.get("embeddings")
    embedding_deployment = _env(environment, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")
    embedding_skip: AzureSmokeResult | None = None
    if not embeddings and responses:
        if not embedding_deployment:
            embedding_skip = _skip("embeddings", "embeddings", ["AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"])
        elif error := _deployment_error(embedding_deployment):
            embedding_skip = AzureSmokeResult("embeddings", "embeddings", "skip", error)
        else:
            embeddings = AzureSmokeCase(
                name="embeddings",
                profile="foundry-embeddings-v1",
                surface="embeddings",
                endpoint=responses.endpoint,
                deployment=embedding_deployment,
                body={},
            )
    if embeddings:
        cases.append(_demo_embedding_case(embeddings))
    elif embedding_skip:
        skips.append(embedding_skip)
    elif skipped := skips_by_surface.get("embeddings"):
        skips.append(skipped)
    return cases, skips


def planned_cases(
    environment: Mapping[str, str],
    *,
    suite: str = "smoke",
) -> tuple[list[AzureSmokeCase], list[AzureSmokeResult]]:
    """Build runnable cases and explicit skips for the selected suite."""
    if suite == "smoke":
        return _planned_smoke_cases(environment)
    if suite == "demo":
        return _planned_demo_cases(environment)
    raise ValueError(f"unknown Azure suite: {suite}")


def _select_surfaces(
    cases: Sequence[AzureSmokeCase],
    skips: Sequence[AzureSmokeResult],
    surfaces: Sequence[str] | None,
) -> tuple[list[AzureSmokeCase], list[AzureSmokeResult]]:
    if surfaces is None:
        return list(cases), list(skips)
    selected = set(surfaces)
    unknown = selected - _SURFACE_NAMES
    if unknown:
        raise ValueError(f"unknown Azure smoke surface(s): {', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("at least one Azure smoke surface must be selected")

    def selector_name(surface: str) -> str:
        return "chat" if surface == "chat_completions" else surface

    return (
        [case for case in cases if selector_name(case.surface) in selected],
        [result for result in skips if selector_name(result.surface) in selected],
    )


def _skip(case: str, surface: str, missing: Sequence[str]) -> AzureSmokeResult:
    return AzureSmokeResult(
        case=case,
        surface=surface,
        status="skip",
        detail="missing env vars: " + ", ".join(dict.fromkeys(missing)),
    )


def _case_url(case: AzureSmokeCase) -> str:
    endpoint = case.endpoint.rstrip("/")
    deployment = parse.quote(case.deployment, safe="")
    if case.surface == "responses":
        return f"{endpoint}/responses"
    if case.profile == "foundry-embeddings-v1":
        return f"{endpoint}/embeddings"
    if case.profile == "foundry-chat-v1":
        return f"{endpoint}/chat/completions"
    if case.surface == "embeddings":
        return f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version={case.api_version}"
    return f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={case.api_version}"


def _adapter_for(case: AzureSmokeCase):
    if case.surface == "responses":
        return AzureOpenAIResponsesAdapter(deployment=case.deployment)
    if case.surface == "embeddings":
        return AzureOpenAIEmbeddingsAdapter(deployment=case.deployment)
    return AzureOpenAIChatCompletionsAdapter(deployment=case.deployment)


def _request_for(case: AzureSmokeCase, api_key: str, *, previous_response_id: str | None = None) -> request.Request:
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
    }
    body = dict(case.body)
    if previous_response_id:
        body["previous_response_id"] = previous_response_id
    return request.Request(
        _case_url(case),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=headers,
    )


def _responses_stream_payload(events: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") not in {"response.completed", "response.incomplete", "response.failed"}:
            continue
        response = event.get("response")
        if isinstance(response, dict):
            return response
    return None


def _responses_stream_text(event: Any) -> str | None:
    if isinstance(event, Mapping) and event.get("type") == "response.output_text.delta":
        delta = event.get("delta")
        return delta if isinstance(delta, str) else None
    return None


def classify_live_error(status: int | None, detail: str) -> str:
    """Return a stable, low-cardinality failure label for Azure live calls."""
    text = detail.lower()
    if status in {401, 403}:
        return "auth_failure"
    if status == 404:
        return "deployment_or_endpoint_not_found"
    if status == 408 or "timed out" in text or "timeout" in text:
        return "timeout"
    if status == 429:
        return "rate_limited_or_quota"
    if status == 400 and "content_filter" in text:
        return "content_filter"
    if status is not None:
        return "provider_http_error"
    if "name or service not known" in text or "getaddrinfo" in text:
        return "dns_failure"
    return "network_or_client_failure"


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _collector_base_url(value: str) -> str:
    """Normalize a local collector URL without risking bearer disclosure off-host."""
    split = parse.urlsplit(value.strip())
    if split.scheme not in {"http", "https"} or not split.hostname:
        raise ValueError("collector URL must be an absolute http(s) URL")
    if split.username or split.password or split.query or split.fragment:
        raise ValueError("collector URL must not contain credentials, query, or fragment")
    try:
        is_loopback = ipaddress.ip_address(split.hostname).is_loopback
    except ValueError:
        is_loopback = split.hostname.lower() == "localhost"
    if not is_loopback:
        raise ValueError("Azure demo publishing is restricted to a loopback collector")

    path = split.path.rstrip("/")
    for suffix in ("/v1/events", "/v1/stats"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path:
        raise ValueError("collector URL must point to the collector root or /v1/events")
    return parse.urlunsplit((split.scheme, split.netloc, "", "", ""))


def _collector_json(
    url: str,
    *,
    auth_token: str,
    opener: Opener,
    timeout: float,
    payload: Any | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {auth_token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with opener(req, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read().decode("utf-8", "replace")
    except urlerr.HTTPError as exc:
        raise RuntimeError(f"collector_http_error:{exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - converted to a stable demo failure
        raise RuntimeError(f"collector_network_error:{type(exc).__name__}") from exc
    if status < 200 or status >= 300:
        raise RuntimeError(f"collector_http_error:{status}")
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("collector_invalid_json") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("collector_invalid_response")
    return decoded


def _publish_to_collector(
    events: Sequence[TokenEvent],
    *,
    trace_id: str,
    collector_url: str,
    auth_token: str,
    opener: Opener,
    timeout: float,
) -> dict[str, int | str]:
    """Publish successful demo events and prove their trace total from the ledger."""
    if not events:
        raise RuntimeError("collector_no_successful_events")
    if any(event.trace_id != trace_id for event in events):
        raise RuntimeError("collector_trace_mismatch")

    base = _collector_base_url(collector_url)
    before = _collector_json(
        f"{base}/v1/stats?summary=1",
        auth_token=auth_token,
        opener=opener,
        timeout=timeout,
    )
    write = _collector_json(
        f"{base}/v1/events",
        auth_token=auth_token,
        opener=opener,
        timeout=timeout,
        payload=[event.to_dict() for event in events],
    )
    expected_ids = {event.event_id for event in events}
    acked = write.get("acked")
    persisted = write.get("persisted")
    rejected = write.get("rejected")
    if not isinstance(acked, list) or not expected_ids.issubset({item for item in acked if isinstance(item, str)}):
        raise RuntimeError("collector_ack_incomplete")
    if not isinstance(persisted, list) or not isinstance(rejected, int) or rejected != 0:
        raise RuntimeError("collector_write_unverified")

    after = _collector_json(
        f"{base}/v1/stats",
        auth_token=auth_token,
        opener=opener,
        timeout=timeout,
    )
    traces = after.get("traces")
    expected_trace_tokens = sum(event.event_contributing_tokens for event in events)
    actual_trace_tokens = traces.get(trace_id) if isinstance(traces, dict) else None
    if actual_trace_tokens != expected_trace_tokens:
        raise RuntimeError("collector_trace_total_mismatch")
    before_total = before.get("total")
    after_total = after.get("total")
    if not isinstance(before_total, int) or not isinstance(after_total, int):
        raise RuntimeError("collector_total_missing")
    return {
        "url": base,
        "persisted": len([item for item in persisted if isinstance(item, str)]),
        "trace_tokens": actual_trace_tokens,
        "total_before": before_total,
        "total_after": after_total,
    }


def _response_header(headers: Any, *names: str) -> str | None:
    for name in names:
        try:
            value = headers.get(name)
        except AttributeError:
            value = None
        if value:
            return str(value)
    return None


def _provider_response_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    return str(value) if value else None


def _chat_incomplete_reason(payload: Mapping[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    reasons = sorted(
        {
            str(choice.get("finish_reason"))
            for choice in choices
            if isinstance(choice, dict) and choice.get("finish_reason") in {"length", "content_filter"}
        }
    )
    return ",".join(reasons) if reasons else None


def _response_text(payload: Mapping[str, Any]) -> str:
    fragments: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    fragments.append(part["text"])
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                fragments.append(message["content"])
    return "\n".join(fragment for fragment in fragments if fragment.strip()).strip()


def _validate_case_response(case: AzureSmokeCase, payload: Mapping[str, Any]) -> str | None:
    expectation = case.expectation
    kind = expectation.get("kind")
    if not kind:
        return None
    if kind == "text":
        return None if _response_text(payload) else "expected non-empty assistant text"
    if kind == "json":
        text = _response_text(payload)
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return "expected valid JSON assistant output"
        if not isinstance(decoded, dict):
            return "expected a JSON object"
        missing = [key for key in expectation.get("required_keys", []) if key not in decoded]
        if missing:
            return f"JSON output missing keys: {', '.join(missing)}"
        mismatches = [
            f"{key}={decoded.get(key)!r} (expected {expected!r})"
            for key, expected in expectation.get("expected_values", {}).items()
            if decoded.get(key) != expected
        ]
        return "incorrect JSON values: " + ", ".join(mismatches) if mismatches else None
    if kind == "function_call":
        expected_name = expectation.get("tool_name")
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                name = item.get("name")
                if name == expected_name:
                    return None
        return f"expected function call: {expected_name}"
    if kind == "embeddings":
        data = payload.get("data")
        expected_count = expectation.get("count")
        if isinstance(data, list) and len(data) == expected_count:
            return None
        actual = len(data) if isinstance(data, list) else 0
        return f"expected {expected_count} embeddings, got {actual}"
    return f"unknown response expectation: {kind}"


def _normalize_success(
    case: AzureSmokeCase,
    payload: dict[str, Any],
    ctx: TraceContext,
    *,
    http_status: int,
    duration_ms: float,
    headers: Any,
    region: str | None,
    previous_response_id: str | None,
) -> TokenEvent:
    provider_status = payload.get("status") if case.surface == "responses" else None
    observation_status = "complete"
    observation_extra: dict[str, Any] = {}
    if provider_status in {"incomplete", "failed"}:
        observation_status = str(provider_status)
        observation_extra["provider_status"] = str(provider_status)
    incomplete_details = payload.get("incomplete_details")
    if isinstance(incomplete_details, dict) and isinstance(incomplete_details.get("reason"), str):
        observation_extra["provider_incomplete_reason"] = incomplete_details["reason"]
    chat_incomplete_reason = _chat_incomplete_reason(payload) if case.surface == "chat_completions" else None
    if chat_incomplete_reason:
        observation_status = "incomplete"
        observation_extra["provider_status"] = "incomplete"
        observation_extra["provider_incomplete_reason"] = chat_incomplete_reason
    observation = Observation(
        authoritative=True,
        status=observation_status,
        http_status=http_status,
        duration_ms=round(duration_ms, 3),
        provider_request_id=_response_header(headers, "apim-request-id", "x-ms-request-id", "request-id", "x-request-id"),
        provider_response_id=_provider_response_id(payload),
        service_name=case.service_name,
        cloud_provider="azure",
        region=region,
        deployment=case.deployment,
        extra={
            **observation_extra,
            "scenario": case.name,
            "profile": case.profile,
            **({"use_case": case.use_case} if case.use_case else {}),
            **({"conversation_id": case.conversation_id} if case.conversation_id else {}),
            **({"conversation_step": case.conversation_step} if case.conversation_step is not None else {}),
            **({"provider_previous_response_id": previous_response_id} if previous_response_id else {}),
        },
    )
    return normalize(
        payload,
        _adapter_for(case),
        context=ctx,
        timestamp=_timestamp(),
        observation=observation.to_dict(),
    )


def _error_event(
    case: AzureSmokeCase,
    ctx: TraceContext,
    *,
    status: int | None,
    duration_ms: float,
    error_code: str,
    detail: str,
    region: str | None,
) -> TokenEvent:
    return TokenEvent(
        event_id=f"azure-smoke-{case.name}-{ctx.request_correlation_id}",
        request_correlation_id=ctx.request_correlation_id,
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        workflow=ctx.workflow,
        environment=ctx.environment,
        provider="azure_openai",
        api_surface=case.surface,
        quantities=[],
        provider_total_tokens=None,
        data_quality_flags=[error_code],
        timestamp=_timestamp(),
        observation=Observation(
            authoritative=False,
            status="failed",
            http_status=status,
            duration_ms=round(duration_ms, 3),
            provider_error_code=error_code,
            service_name=case.service_name,
            cloud_provider="azure",
            region=region,
            deployment=case.deployment,
            extra={
                "failure_detail": detail[:500],
                "scenario": case.name,
                "profile": case.profile,
                **({"use_case": case.use_case} if case.use_case else {}),
            },
        ),
    )


def _run_case(
    case: AzureSmokeCase,
    *,
    api_key: str,
    out_dir: Path,
    opener: Opener,
    timeout: float,
    root_context: TraceContext,
    region: str | None,
    previous_response_id: str | None = None,
) -> tuple[AzureSmokeResult, TokenEvent | None, str | None]:
    ctx = root_context.child_span()
    raw_path = out_dir / "raw" / f"{case.name}.json"
    req = _request_for(case, api_key, previous_response_id=previous_response_id)
    stream_events: list[dict[str, Any]] | None = None
    started = time.perf_counter()
    try:
        with opener(req, timeout=timeout) as response:
            raw_body = response.read()
            duration_ms = (time.perf_counter() - started) * 1000
            if case.stream:
                stream_events = parse_sse_json(raw_body)
                payload = _responses_stream_payload(stream_events) or {}
            else:
                payload = json.loads(raw_body.decode("utf-8", "replace"))
            http_status = int(getattr(response, "status", 200))
            headers = getattr(response, "headers", {})
    except urlerr.HTTPError as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        detail = exc.read().decode("utf-8", "replace")
        error_code = classify_live_error(exc.code, detail)
        event = _error_event(case, ctx, status=exc.code, duration_ms=duration_ms, error_code=error_code, detail=detail, region=region)
        artifact = _write_json(
            raw_path,
            {
                "case": asdict(case),
                "captured_at": _timestamp(),
                "status": "fail",
                "http_status": exc.code,
                "error_code": error_code,
                "error_detail": detail[:2000],
            },
        )
        return (
            AzureSmokeResult(case.name, case.surface, "fail", error_code, exc.code, event.event_id, artifact=artifact),
            event,
            None,
        )
    except Exception as exc:  # noqa: BLE001 - classify live failures into the report
        duration_ms = (time.perf_counter() - started) * 1000
        detail = f"{type(exc).__name__}: {exc}"
        error_code = classify_live_error(None, detail)
        event = _error_event(case, ctx, status=None, duration_ms=duration_ms, error_code=error_code, detail=detail, region=region)
        artifact = _write_json(
            raw_path,
            {
                "case": asdict(case),
                "captured_at": _timestamp(),
                "status": "fail",
                "error_code": error_code,
                "error_detail": detail,
            },
        )
        return (
            AzureSmokeResult(case.name, case.surface, "fail", error_code, None, event.event_id, artifact=artifact),
            event,
            None,
        )

    if case.stream:
        event = consume_stream(
            stream_events or (),
            _adapter_for(case),
            context=ctx,
            text_extractor=_responses_stream_text,
            model=case.deployment,
        )
        event.observation.update(
            {
                "http_status": http_status,
                "duration_ms": round(duration_ms, 3),
                "provider_request_id": _response_header(
                    headers,
                    "apim-request-id",
                    "x-ms-request-id",
                    "request-id",
                    "x-request-id",
                ),
                "provider_response_id": _provider_response_id(payload),
                "service_name": case.service_name,
                "cloud_provider": "azure",
                "region": region,
                "deployment": case.deployment,
                "scenario": case.name,
                "profile": case.profile,
                **({"use_case": case.use_case} if case.use_case else {}),
                **({"conversation_id": case.conversation_id} if case.conversation_id else {}),
                **({"conversation_step": case.conversation_step} if case.conversation_step is not None else {}),
                **({"provider_previous_response_id": previous_response_id} if previous_response_id else {}),
                "stream_event_count": len(stream_events or ()),
            }
        )
    else:
        event = _normalize_success(
            case,
            payload,
            ctx,
            http_status=http_status,
            duration_ms=duration_ms,
            headers=headers,
            region=region,
            previous_response_id=previous_response_id,
        )
    mismatch = event.event_total_mismatch
    semantic_error = _validate_case_response(case, payload)
    terminal_failure_flags = {
        "provider_response_incomplete",
        "provider_response_failed",
    }
    response_state_failure = terminal_failure_flags.intersection(event.data_quality_flags)
    usage_evidence_failure = (
        not event.quantities
        or event.provider_total_tokens is None
        or mismatch != 0
        or any(
            quantity.quantity is None or quantity.precision_level != PrecisionLevel.EXACT
            for quantity in event.quantities
        )
        or "provider_usage_missing" in event.data_quality_flags
    )
    stream_evidence_failure = case.stream and (
        not stream_events
        or _responses_stream_payload(stream_events) is None
        or "provider_stream_usage_missing" in event.data_quality_flags
    )
    failed = (
        usage_evidence_failure
        or bool(event.over_attributed_tokens)
        or bool(response_state_failure)
        or stream_evidence_failure
        or semantic_error is not None
    )
    status = "fail" if failed else "pass"
    if response_state_failure:
        reason = event.observation.get("provider_incomplete_reason")
        detail = "provider response " + ",".join(sorted(response_state_failure))
        if reason:
            detail += f" ({reason})"
    elif stream_evidence_failure:
        detail = "stream proof failed: terminal response usage was not observed"
    elif usage_evidence_failure:
        detail = "provider usage was incomplete or did not reconcile exactly"
    elif semantic_error:
        detail = f"scenario validation failed: {semantic_error}"
    else:
        detail = "streamed, normalized and reconciled" if case.stream else "normalized and reconciled"
        if failed:
            detail = f"normalization mismatch={mismatch}"
        if not failed and case.conversation_id:
            answer = " ".join(_response_text(payload).split())[:300]
            detail += f" | answer={answer}"
    artifact = _write_json(
        raw_path,
        {
            "case": asdict(case),
            "captured_at": _timestamp(),
            "status": status,
            "detail": detail,
            "http_status": http_status,
            "previous_response_id": previous_response_id,
            "response": payload,
            **({"stream_events": stream_events} if case.stream else {}),
        },
    )
    return (
        AzureSmokeResult(
            case=case.name,
            surface=case.surface,
            status=status,
            detail=detail,
            http_status=http_status,
            event_id=event.event_id,
            contributing_tokens=event.event_contributing_tokens,
            provider_total_tokens=event.provider_total_tokens,
            data_quality_flags=list(event.data_quality_flags),
            artifact=artifact,
        ),
        event,
        _provider_response_id(payload),
    )


def _write_audit_readme(path: Path, summary: AzureSmokeSummary) -> str:
    lines = [
        f"# Azure {summary.suite.title()} Audit Bundle",
        "",
        f"- suite: {summary.suite}",
        f"- passed: {summary.passed}",
        f"- ran_count: {summary.ran_count}",
        f"- skipped_count: {summary.skipped_count}",
        f"- failure_count: {summary.failure_count}",
        f"- event_count: {summary.event_count}",
        f"- observed_total_contributing_tokens: {summary.observed_total_contributing_tokens}",
        f"- trace_id: {summary.trace_id or '-'}",
        f"- collector_status: {summary.collector_status}",
        f"- collector_trace_tokens: {summary.collector_trace_tokens}",
        "",
        "## Cases",
        "",
    ]
    for result in summary.results:
        lines.append(
            f"- {result.case} ({result.surface}): {result.status} - {result.detail}; "
            f"tokens={result.contributing_tokens}; flags={result.data_quality_flags or '-'}"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    for name, artifact in summary.artifacts.items():
        lines.append(f"- {name}: `{artifact}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_smoke(
    *,
    out_dir: str | None = None,
    environment: Mapping[str, str] | None = None,
    opener: Opener | None = None,
    collector_opener: Opener | None = None,
    timeout: float = 30.0,
    collector_timeout: float = 10.0,
    dry_run: bool = False,
    require_live: bool = False,
    surfaces: Sequence[str] | None = None,
    collector_url: str | None = None,
    suite: str = "smoke",
) -> AzureSmokeSummary:
    """Run live Azure smoke cases and write an audit bundle."""
    if timeout <= 0 or collector_timeout <= 0:
        raise ValueError("timeouts must be positive")
    if suite not in _SUITE_NAMES:
        raise ValueError(f"unknown Azure suite: {suite}")
    env = dict(os.environ if environment is None else environment)
    generated_at = _timestamp()
    code_fingerprint = runtime_fingerprint()
    root = Path(out_dir or Path("runs") / "azure-smoke" / _safe_timestamp_for_path()).resolve()
    cases, skip_results = planned_cases(env, suite=suite)
    cases, skip_results = _select_surfaces(cases, skip_results, surfaces)
    normalized_collector_url = _collector_base_url(collector_url) if collector_url else None
    artifacts: dict[str, str] = {}
    root.mkdir(parents=True, exist_ok=True)
    artifacts["config"] = _write_json(root / "config_redacted.json", _redacted_config(env))
    artifacts["plan"] = _write_json(
        root / "plan.json",
        {
            "cases": [asdict(case) for case in cases],
            "skips": [asdict(s) for s in skip_results],
            "selected_surfaces": list(surfaces) if surfaces is not None else None,
            "collector_url": normalized_collector_url,
            "suite": suite,
        },
    )

    if dry_run:
        results = [AzureSmokeResult(case.name, case.surface, "skip", "dry-run: no live call executed") for case in cases] + skip_results
        summary = AzureSmokeSummary(
            out_dir=str(root),
            passed=not require_live,
            ran_count=0,
            skipped_count=len(results),
            failure_count=1 if require_live else 0,
            event_count=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            results=results,
            suite=suite,
            collector_url=normalized_collector_url,
            collector_status="dry_run" if normalized_collector_url else "not_requested",
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
        artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
        artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
        capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
        if capture_key:
            artifacts["capture_attestation"] = write_capture_attestation(
                artifacts["summary"], capture_key, harness="azure_smoke"
            )
        return summary

    api_key = _env(env, "AZURE_OPENAI_API_KEY")
    http_opener = opener or request.urlopen
    workflow = f"azure-{suite}"
    root_context = new_trace(workflow=workflow, environment="live")
    region = _env(env, "AZURE_REGION")
    events: list[TokenEvent] = []
    publishable_events: list[TokenEvent] = []
    results: list[AzureSmokeResult] = []
    provider_response_ids: dict[str, str] = {}
    for case in cases:
        if not api_key:
            continue
        previous_response_id = provider_response_ids.get(case.previous_case) if case.previous_case else None
        if case.previous_case and not previous_response_id:
            results.append(
                AzureSmokeResult(
                    case.name,
                    case.surface,
                    "skip",
                    f"conversation dependency unavailable: {case.previous_case}",
                )
            )
            continue
        result, event, provider_response_id = _run_case(
            case,
            api_key=api_key,
            out_dir=root,
            opener=http_opener,
            timeout=timeout,
            root_context=root_context,
            region=region,
            previous_response_id=previous_response_id,
        )
        results.append(result)
        if provider_response_id:
            provider_response_ids[case.name] = provider_response_id
        if event is not None:
            events.append(event)
            if event.is_authoritative and event.quantities:
                publishable_events.append(event)
    results.extend(skip_results)

    if events:
        store_path = root / "events.jsonl"
        FileRepository(str(store_path)).append_many(events)
        artifacts["events_jsonl"] = str(store_path)
        trace = Trace(trace_id=root_context.trace_id, workflow=workflow, environment="live", events=events)
        csv_dir = root / "csv"
        export_csv(trace, str(csv_dir))
        artifacts["csv_dir"] = str(csv_dir)
        excel_path = root / "azure_smoke.xlsx"
        export_excel(trace, str(excel_path))
        artifacts["excel"] = str(excel_path)
        trust_report = build_trust_report(trace).to_dict()
        artifacts["trust_report"] = _write_json(root / "trust_report.json", trust_report)
        observed_total = trust_report["observed_total_contributing_tokens"]
    else:
        observed_total = 0

    ran_count = sum(1 for result in results if not result.skipped)
    failure_count = sum(1 for result in results if result.failed)
    if require_live and ran_count == 0:
        failure_count += 1
    collector_status = "not_requested"
    collector_detail: str | None = None
    collector_persisted = 0
    collector_trace_tokens: int | None = None
    collector_total_before: int | None = None
    collector_total_after: int | None = None
    if normalized_collector_url:
        collector_status = "failed"
        try:
            auth_token = load_auth_token(
                env,
                allow_default_file=environment is None or bool(_env(env, "TRACKER_AUTH_TOKEN_FILE")),
            )
            if not auth_token:
                raise RuntimeError("collector_auth_token_missing")
            publish = _publish_to_collector(
                publishable_events,
                trace_id=root_context.trace_id,
                collector_url=normalized_collector_url,
                auth_token=auth_token,
                opener=collector_opener or request.urlopen,
                timeout=collector_timeout,
            )
            collector_status = "published"
            collector_detail = "published and trace total verified"
            collector_persisted = int(publish["persisted"])
            collector_trace_tokens = int(publish["trace_tokens"])
            collector_total_before = int(publish["total_before"])
            collector_total_after = int(publish["total_after"])
        except (RuntimeError, ValueError) as exc:
            collector_detail = str(exc)[:300]
            failure_count += 1
    summary = AzureSmokeSummary(
        out_dir=str(root),
        passed=failure_count == 0,
        ran_count=ran_count,
        skipped_count=sum(1 for result in results if result.skipped),
        failure_count=failure_count,
        event_count=len(events),
        observed_total_contributing_tokens=observed_total,
        artifacts=artifacts,
        results=results,
        suite=suite,
        trace_id=root_context.trace_id,
        collector_url=normalized_collector_url,
        collector_status=collector_status,
        collector_detail=collector_detail,
        collector_persisted_event_count=collector_persisted,
        collector_trace_tokens=collector_trace_tokens,
        collector_total_before=collector_total_before,
        collector_total_after=collector_total_after,
        generated_at=generated_at,
        runtime_fingerprint=code_fingerprint,
    )
    artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
    artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
    capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
    if capture_key:
        artifacts["capture_attestation"] = write_capture_attestation(
            artifacts["summary"], capture_key, harness="azure_smoke"
        )
    return summary


def _render_text(summary: AzureSmokeSummary) -> str:
    lines = [f"Azure OpenAI {summary.suite} harness"]
    for result in summary.results:
        detail = f"[{result.status.upper()}] {result.case}: {result.detail}"
        if not result.skipped:
            flags = ",".join(result.data_quality_flags) if result.data_quality_flags else "none"
            detail += (
                f" | tokens={result.contributing_tokens}"
                f" provider_total={result.provider_total_tokens} flags={flags}"
            )
        lines.append(detail)
    if summary.collector_url:
        collector = f"collector: status={summary.collector_status}"
        if summary.collector_status == "published":
            collector += (
                f" persisted={summary.collector_persisted_event_count}"
                f" trace_tokens={summary.collector_trace_tokens}"
                f" total_before={summary.collector_total_before}"
                f" total_after={summary.collector_total_after}"
            )
        elif summary.collector_detail:
            collector += f" detail={summary.collector_detail}"
        lines.append(collector)
        if summary.trace_id:
            lines.append(f"trace_id: {summary.trace_id}")
    lines.append(
        "summary: "
        f"passed={summary.passed} ran={summary.ran_count} skipped={summary.skipped_count} "
        f"failures={summary.failure_count} tokens={summary.observed_total_contributing_tokens}"
    )
    lines.append(f"artifacts: {summary.out_dir}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a tiny live Azure OpenAI smoke test and write an audit bundle")
    parser.add_argument("--out-dir", help="audit bundle directory; default runs/azure-smoke/<timestamp>")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true", help="write plan/config only; no live calls")
    parser.add_argument("--require-live", action="store_true", help="return non-zero if no live surface can run")
    parser.add_argument(
        "--suite",
        choices=sorted(_SUITE_NAMES),
        default="smoke",
        help="smoke runs one tiny call per surface; demo runs realistic multi-service scenarios",
    )
    parser.add_argument(
        "--surface",
        action="append",
        choices=sorted(_SURFACE_NAMES),
        help="run only this surface; repeat to select more than one",
    )
    parser.add_argument("--collector-url", help="publish successful events to the local collector and verify the trace")
    parser.add_argument("--collector-timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = run_smoke(
        out_dir=args.out_dir,
        timeout=args.timeout,
        collector_timeout=args.collector_timeout,
        dry_run=args.dry_run,
        require_live=args.require_live,
        surfaces=args.surface,
        collector_url=args.collector_url,
        suite=args.suite,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) if args.json else _render_text(summary))
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
