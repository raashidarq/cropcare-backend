"""
Typed failures for the AI provider layer.

Every provider (dependencies/ai/gemini_provider.py, nvidia_provider.py)
raises ONLY these, never a raw SDK/HTTP exception - that's the whole point
of the abstraction: dependencies/ai/service.py decides whether to fall back
to a second provider by catching these types, not by re-parsing error text
that differs per provider/SDK.

The one distinction that actually matters for routing: is this something a
DIFFERENT provider might succeed at (AIConfigurationError,
AIProviderUnavailable, AIQuotaExceeded - all worth a fallback attempt), or
is it a bug that a different provider would hit identically
(AIRequestFailed - never worth a fallback, since silently retrying a bad
prompt against a second provider would turn a real, fixable bug into a
confusing, hard-to-diagnose one further downstream).
"""

from __future__ import annotations


class AIError(Exception):
    """Base class. Never raised directly - always one of the subtypes."""


class AIConfigurationError(AIError):
    """This provider has no usable configuration (no API key, unknown
    provider name). Treated the same as "unavailable" for fallback
    purposes: a missing key on the primary should not fail the farmer's
    request when a fallback is configured and working.
    """


class AIProviderUnavailable(AIError):
    """The provider could not serve the request right now: a timeout, a
    connection failure, a 5xx, an auth rejection, or every one of a
    provider's own internal retries (model names, keys) exhausted for a
    transient reason. Worth trying a different provider.
    """


class AIQuotaExceeded(AIProviderUnavailable):
    """A specific case of "unavailable": the provider rejected the request
    for quota/rate-limit reasons (HTTP 429). Kept as its own type, not
    folded into AIProviderUnavailable, so logs can distinguish "we're
    being throttled" from "the network/provider is broken" - the two call
    for different fixes even though both currently trigger a fallback.
    """


class AIRequestFailed(AIError):
    """The provider responded, but the request or its response was invalid
    in a way that points at a bug, not provider unavailability: a
    malformed prompt, a response body the provider itself could not fill
    in reasonably, or similar. NOT retried against the fallback provider -
    a different provider would fail on the same bad input, and a fallback
    that papers over that would only make the real bug harder to find.
    """
