"""LLM-written explanation of an opportunity (optional, off the critical path).

The model receives ONLY the structured facts computed by the deterministic engine (no free text from the
internet, so no prompt-injection surface) and must explain them in French. Its output is schema-validated.
It never decides anything: the explanation is displayed next to the facts it was given, so the user can
check it, and it is never read by the risk engine or the execution layer.

Disabled unless ``APEX_LLM_ENABLED=true`` and an Anthropic credential is available (``ANTHROPIC_API_KEY``).
One call per opportunity (cached on the opportunity), so cost is bounded by the number of opportunities.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam
from pydantic import BaseModel, Field, ValidationError

from apex.analysis.opportunities import Opportunity
from apex.core.logging import get_logger

log = get_logger(__name__)

SYSTEM = """Tu es l'analyste d'une plateforme de trading de l'or (XAU/USD). On te donne une opportunité détectée \
par un moteur d'analyse déterministe, sous forme de faits structurés (JSON).

Ta tâche : expliquer en français, clairement et brièvement, POURQUOI cette opportunité a été détectée, ce qui \
la soutient, ce qui la fragilise, et ce qui l'invaliderait.

Règles strictes :
- N'utilise QUE les faits fournis. N'invente aucun prix, niveau, statistique, actualité ou indicateur absent.
- Les « qualité » et « confluences » sont des heuristiques, pas des probabilités : ne parle jamais de \
probabilité de gain, de certitude ou de profit garanti.
- Si les statistiques historiques sont absentes ou marquées « small_sample », dis que l'avantage n'est pas prouvé.
- Si des bloqueurs existent, commence par dire que le trading est actuellement interdit et pourquoi.
- Pas de conseil en investissement personnalisé ; reste factuel et prudent."""


class Narrative(BaseModel):
    resume: str = Field(description="2 phrases maximum : l'idée de trade et sa logique")
    pourquoi: list[str] = Field(description="3 à 5 raisons, chacune appuyée sur un fait fourni")
    risques: list[str] = Field(description="2 à 4 points faibles ou conflits")
    invalidation: str = Field(description="ce qui invaliderait l'idée")
    prudence: str = Field(description="une phrase sur les limites de cette analyse")
    model: str = ""


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resume": {"type": "string"},
        "pourquoi": {"type": "array", "items": {"type": "string"}},
        "risques": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
        "prudence": {"type": "string"},
    },
    "required": ["resume", "pourquoi", "risques", "invalidation", "prudence"],
    "additionalProperties": False,
}


def facts(opp: Opportunity) -> dict[str, Any]:
    d = opp.to_dict()
    keep = (
        "setup_label",
        "direction",
        "timeframe",
        "detected_at",
        "entry",
        "stop",
        "targets",
        "risk_reward",
        "quality",
        "confluences",
        "warnings",
        "blockers",
        "invalidation",
        "stats",
    )
    out: dict[str, Any] = json.loads(json.dumps({k: d[k] for k in keep}, default=str))
    return out


class Explainer:
    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: Literal["low", "medium", "high"] = "low",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model, self.effort = model, effort
        self.client = client or anthropic.AsyncAnthropic()

    async def explain(self, opp: Opportunity) -> Narrative | None:
        output_config: BetaOutputConfigParam = {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        }
        messages: list[BetaMessageParam] = [
            {
                "role": "user",
                "content": "Faits de l'opportunité :\n" + json.dumps(facts(opp), ensure_ascii=False),
            }
        ]
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config=output_config,
                messages=messages,
            )
        except anthropic.RateLimitError:
            log.warning("explainer_rate_limited")
            return None
        except anthropic.APIStatusError as e:
            log.warning("explainer_api_error", status=e.status_code)
            return None
        except anthropic.APIConnectionError:
            log.warning("explainer_unreachable")
            return None
        if response.stop_reason == "refusal":
            log.warning("explainer_refused")
            return None
        if response.stop_reason == "max_tokens":
            log.warning("explainer_truncated")
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return None
        try:
            n = Narrative.model_validate_json(text)
        except ValidationError:
            log.warning("explainer_invalid_output")
            return None
        n.model = response.model
        return n
