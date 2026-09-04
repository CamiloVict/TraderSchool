"""The contract for an LLM interpretation layer. No provider attached.

Nothing here calls a model. This module defines the shape of the
conversation so that when a provider is wired in later, the boundary
is already fixed and narrow:

    context snapshot -> build_llm_input() -> [model] -> parse_llm_output()

What the LLM is allowed to do: interpret, explain, classify the
scenario, spot contradictions between the pieces of evidence, and
summarize. What it must never do: compute an indicator, invent a price
level, decide position size, or override a risk limit. Those are
deterministic and already settled by the time this input is built
(master prompt sections 3.2 and 23).

`parse_llm_output` is strict on purpose. A model that returns prose
where the schema says JSON, or a direction outside the allowed set,
raises — a downstream system must never receive a half-understood
answer that looks structured.
"""
import json

from context_engine.params import CONTEXT_ENGINE_VERSION
from context_engine.schema import ContextSnapshot

# Bumped whenever the prompt or the expected output shape changes, so a
# stored interpretation records which contract produced it.
PROMPT_VERSION = "0.1.0"

REQUIRED_OUTPUT_FIELDS = (
    "interpretation",
    "market_state",
    "preferred_direction",
    "confidence",
    "preferred_setups",
    "avoid",
    "invalidation",
    "contradictions",
)

ALLOWED_DIRECTIONS = ("LONG", "SHORT", "NONE")

SYSTEM_INSTRUCTIONS = (
    "You interpret a precomputed market context. Every number you are given "
    "is already final: never recalculate, adjust or invent prices, levels, "
    "indicator values or risk limits. Reason only from the supplied fields. "
    "If the evidence is contradictory, say so and prefer NONE over forcing a "
    "direction. Reply with a single JSON object matching the requested "
    "schema and nothing else."
)


def build_llm_input(snapshot: ContextSnapshot, candidate_setups: list = None) -> dict:
    """Structured payload for the model.

    Only the interpretable summary is sent, not raw candles: the model
    has no business re-deriving structure, and sending price series
    would invite exactly that.
    """
    data = snapshot.to_dict()

    return {
        "prompt_version": PROMPT_VERSION,
        "context_version": data.get("version", CONTEXT_ENGINE_VERSION),
        "instructions": SYSTEM_INSTRUCTIONS,
        "market_context": {
            "timestamp": data.get("timestamp"),
            "asset": data.get("asset"),
            "regime": data.get("regime"),
            "bias": data.get("bias"),
            "context_score": data.get("context_score"),
            "market_state": data.get("market_state"),
            "preferred_direction": data.get("preferred_direction"),
            "avoid": data.get("avoid"),
            "no_trade": data.get("no_trade"),
        },
        "multi_timeframe": data.get("multi_timeframe"),
        "alignment": data.get("alignment"),
        "structure": data.get("structure"),
        "liquidity": data.get("liquidity"),
        "volatility": data.get("volatility"),
        "range": data.get("range"),
        "sessions": data.get("sessions"),
        "events": data.get("events"),
        "data_quality": data.get("data_quality"),
        "candidate_setups": list(candidate_setups or []),
        "expected_output_schema": {field: None for field in REQUIRED_OUTPUT_FIELDS},
    }


class LLMOutputError(ValueError):
    """Raised when a model reply does not satisfy the contract."""


def parse_llm_output(raw: str) -> dict:
    """Validate and parse a model reply.

    Rejects anything that is not a JSON object with every required
    field, a known direction, and a confidence in [0, 1]. The engine's
    own deterministic output is unaffected either way — this is an
    advisory layer, and a rejected reply simply means there is no
    interpretation to show.
    """
    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMOutputError(f"reply is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise LLMOutputError(f"expected a JSON object, got {type(payload).__name__}")

    missing = [field for field in REQUIRED_OUTPUT_FIELDS if field not in payload]
    if missing:
        raise LLMOutputError(f"reply is missing required field(s): {', '.join(missing)}")

    direction = payload["preferred_direction"]
    if direction not in ALLOWED_DIRECTIONS:
        raise LLMOutputError(
            f"preferred_direction must be one of {ALLOWED_DIRECTIONS}, got {direction!r}"
        )

    try:
        confidence = float(payload["confidence"])
    except (TypeError, ValueError) as exc:
        raise LLMOutputError(f"confidence must be a number: {exc}") from exc
    if not 0.0 <= confidence <= 1.0:
        raise LLMOutputError(f"confidence must be within [0, 1], got {confidence}")

    for field in ("preferred_setups", "avoid", "invalidation", "contradictions"):
        if not isinstance(payload[field], list):
            raise LLMOutputError(f"{field} must be a list, got {type(payload[field]).__name__}")

    return {**payload, "confidence": confidence, "prompt_version": PROMPT_VERSION}
