"""
Provider-independent AI access.

Routers call `dependencies.ai.service.generate(prompt, json_mode=...)` and
never import a specific provider (Gemini, NVIDIA) directly - see
`service.py` for why, and `errors.py` for what a caller can expect to catch.
"""
