"""Group L - the structural claims, enforced mechanically rather than asserted.

The README says the decision path contains no model. That is only worth saying if
something checks it, so this file reads the modules and fails if a model client or
an HTTP client ever appears in the authorisation path.
"""

from __future__ import annotations

from pathlib import Path

import causal

DECISION_MODULES = [
    "engine.py",
    "policy.py",
    "ledger.py",
    "registry.py",
    "postconditions.py",
    "reconcile.py",
    "intent.py",
]

MODEL_SDKS = (
    "openai", "anthropic", "google.generativeai", "google.genai", "genai",
    "ollama", "litellm", "cohere", "mistralai", "transformers", "vertexai",
    "boto3", "langchain", "llama_index",
)


def _imports(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            out.append(stripped)
    return out


def test_no_model_client_is_imported_in_the_decision_path() -> None:
    package = Path(causal.__file__).parent
    for name in DECISION_MODULES:
        imports = _imports(package / name)
        offending = [imp for imp in imports if any(sdk in imp for sdk in MODEL_SDKS)]
        assert not offending, f"{name} imports a model client: {offending}"


def test_no_http_client_is_imported_in_the_decision_path() -> None:
    """The authorisation path must not be able to reach the network directly.

    Adapters own the network; the decision path decides. If http ever appears
    here, a decision could start depending on a remote answer.
    """
    package = Path(causal.__file__).parent
    for name in DECISION_MODULES:
        imports = _imports(package / name)
        offending = [imp for imp in imports if imp.startswith(("import httpx", "from httpx",
                                                             "import requests", "from requests",
                                                             "import urllib"))]
        assert not offending, f"{name} imports an HTTP client: {offending}"


def test_adapters_are_the_only_network_boundary() -> None:
    """The complement of the test above: the network lives in adapters, and the
    engine reaches it only through the Apps bundle."""
    package = Path(causal.__file__).parent
    assert any("httpx" in imp for imp in _imports(package / "adapters.py")), \
        "adapters.py should be where the network client lives"
    engine_source = (package / "engine.py").read_text()
    assert "self.apps" in engine_source, "the engine should act through the Apps bundle"
