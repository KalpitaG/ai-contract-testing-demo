"""
Contract Testing Config Reader
================================
Resolves the Pactflow participant name and consumer/provider relationships
for a given repository.

Resolution hierarchy (in order):
  1. .contract-testing.yml in the target repo
  2. config/service-registry.yml (central repo)
  3. Pactflow API query (existing pacts)
  4. Repo name (last-resort fallback)

Usage:
    reader = ContractConfigReader(repo_path="/path/to/provider-repo")
    config = reader.resolve()
    # config.pactflow_name  → "pact-provider-demo"
    # config.provider_to    → ["pact-implementation"]
    # config.consumer_of    → []
    # config.source         → "contract_testing_yml"
"""

import os
import yaml
import requests
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


CONFIG_FILE_NAME = ".contract-testing.yml"

# Path to the central service registry relative to this file
_THIS_DIR = Path(__file__).resolve().parent
_CENTRAL_REPO_ROOT = _THIS_DIR.parent.parent  # src/core -> src -> repo root
SERVICE_REGISTRY_PATH = _CENTRAL_REPO_ROOT / "config" / "service-registry.yml"


@dataclass
class ContractConfig:
    """Resolved configuration for a single repository."""
    pactflow_name: str
    consumer_of: list[dict] = field(default_factory=list)
    """Each entry: {pactflow_name: str, openapi_spec: str (optional)}"""
    provider_to: list[str] = field(default_factory=list)
    """List of consumer pactflow_names this service provides to."""
    source: str = "fallback"
    """Where the config was resolved from:
       contract_testing_yml | service_registry | pactflow_api | repo_name_fallback
    """
    server: dict = field(default_factory=dict)
    """Optional server config: {port, health_path, start_command}"""


class ContractConfigReader:
    """
    Resolves Pactflow participant names and relationships for a repository.

    Args:
        repo_path: Absolute path to the checked-out target repository.
        repo_name: GitHub repo name (e.g. 'pact-provider-demo'). Used as fallback
                   if repo_path is not available.
        pactflow_base_url: Pactflow base URL (env var PACTFLOW_BASE_URL if omitted).
        pactflow_token: Pactflow bearer token (env var PACTFLOW_TOKEN if omitted).
    """

    def __init__(
        self,
        repo_path: Optional[str] = None,
        repo_name: Optional[str] = None,
        pactflow_base_url: Optional[str] = None,
        pactflow_token: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path) if repo_path else None
        self.repo_name = repo_name
        self.pactflow_base_url = pactflow_base_url or os.getenv("PACTFLOW_BASE_URL")
        self.pactflow_token = pactflow_token or os.getenv("PACTFLOW_TOKEN")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self) -> ContractConfig:
        """
        Resolve config using the priority hierarchy.
        Returns a ContractConfig; never raises (falls back to repo name).
        """
        # 1. Per-repo config file
        config = self._read_contract_testing_yml()
        if config:
            print(f"[ConfigReader] Resolved from .contract-testing.yml: {config.pactflow_name}")
            return config

        # 2. Central service registry
        config = self._read_service_registry()
        if config:
            print(f"[ConfigReader] Resolved from service-registry.yml: {config.pactflow_name}")
            return config

        # 3. Pactflow API
        config = self._query_pactflow()
        if config:
            print(f"[ConfigReader] Resolved from Pactflow API: {config.pactflow_name}")
            return config

        # 4. Repo name fallback
        name = self._fallback_name()
        print(f"[ConfigReader] Using repo name fallback: {name}")
        return ContractConfig(pactflow_name=name, source="repo_name_fallback")

    # ------------------------------------------------------------------
    # Resolution strategies
    # ------------------------------------------------------------------

    def _read_contract_testing_yml(self) -> Optional[ContractConfig]:
        """Read .contract-testing.yml from the target repo."""
        if not self.repo_path:
            return None

        config_file = self.repo_path / CONFIG_FILE_NAME
        if not config_file.exists():
            return None

        try:
            with open(config_file) as f:
                data = yaml.safe_load(f) or {}

            pactflow_name = data.get("pactflow_name", "").strip()
            if not pactflow_name:
                return None

            # consumer_of: list of {pactflow_name, openapi_spec}
            raw_consumer_of = data.get("consumer_of", []) or []
            consumer_of = []
            for entry in raw_consumer_of:
                if isinstance(entry, dict):
                    consumer_of.append(entry)
                elif isinstance(entry, str):
                    consumer_of.append({"pactflow_name": entry})

            # provider_to: list of strings
            raw_provider_to = data.get("provider_to", []) or []
            provider_to = [
                (p if isinstance(p, str) else p.get("pactflow_name", ""))
                for p in raw_provider_to
            ]

            server = data.get("server", {}) or {}

            return ContractConfig(
                pactflow_name=pactflow_name,
                consumer_of=consumer_of,
                provider_to=provider_to,
                source="contract_testing_yml",
                server=server,
            )
        except Exception as e:
            print(f"[ConfigReader] WARN: Could not parse .contract-testing.yml: {e}")
            return None

    def _read_service_registry(self) -> Optional[ContractConfig]:
        """Look up repo in the central service-registry.yml."""
        if not SERVICE_REGISTRY_PATH.exists():
            return None

        # Determine the key to look up
        lookup_name = self._fallback_name()
        if not lookup_name:
            return None

        try:
            with open(SERVICE_REGISTRY_PATH) as f:
                registry = yaml.safe_load(f) or {}

            services = registry.get("services", {}) or {}
            entry = services.get(lookup_name)
            if not entry:
                return None

            raw_consumer_of = entry.get("consumer_of", []) or []
            consumer_of = [
                ({"pactflow_name": p} if isinstance(p, str) else p)
                for p in raw_consumer_of
            ]

            raw_provider_to = entry.get("provider_to", []) or []
            provider_to = [
                (p if isinstance(p, str) else p.get("pactflow_name", ""))
                for p in raw_provider_to
            ]

            return ContractConfig(
                pactflow_name=lookup_name,
                consumer_of=consumer_of,
                provider_to=provider_to,
                source="service_registry",
            )
        except Exception as e:
            print(f"[ConfigReader] WARN: Could not parse service-registry.yml: {e}")
            return None

    def _query_pactflow(self) -> Optional[ContractConfig]:
        """Query Pactflow API to discover relationships for this service."""
        if not self.pactflow_base_url or not self.pactflow_token:
            return None

        lookup_name = self._fallback_name()
        if not lookup_name:
            return None

        headers = {
            "Authorization": f"Bearer {self.pactflow_token}",
            "Accept": "application/hal+json",
        }
        timeout = 15

        try:
            # Check provider pacts (pacts where this service is the provider)
            provider_resp = requests.get(
                f"{self.pactflow_base_url}/pacts/provider/{lookup_name}/latest",
                headers=headers,
                timeout=timeout,
            )
            provider_to_consumers: list[str] = []
            if provider_resp.status_code == 200:
                pacts = (
                    provider_resp.json()
                    .get("_embedded", {})
                    .get("pacts", [])
                )
                for pact in pacts:
                    consumer_name = (
                        pact.get("_embedded", {}).get("consumer", {}).get("name", "")
                    )
                    if consumer_name:
                        provider_to_consumers.append(consumer_name)

            # Check consumer pacts (pacts where this service is the consumer)
            # We have to iterate all pacticipants to find providers
            consumer_of_providers: list[dict] = []
            pacticipants_resp = requests.get(
                f"{self.pactflow_base_url}/pacticipants",
                headers=headers,
                timeout=timeout,
            )
            if pacticipants_resp.status_code == 200:
                pacticipants = (
                    pacticipants_resp.json()
                    .get("_embedded", {})
                    .get("pacticipants", [])
                )
                for p in pacticipants:
                    provider_name = p.get("name", "")
                    if not provider_name or provider_name == lookup_name:
                        continue
                    prov_resp = requests.get(
                        f"{self.pactflow_base_url}/pacts/provider/{provider_name}/latest",
                        headers=headers,
                        timeout=timeout,
                    )
                    if prov_resp.status_code == 200:
                        pacts = (
                            prov_resp.json()
                            .get("_embedded", {})
                            .get("pacts", [])
                        )
                        for pact in pacts:
                            c = (
                                pact.get("_embedded", {})
                                .get("consumer", {})
                                .get("name", "")
                            )
                            if c == lookup_name:
                                consumer_of_providers.append({"pactflow_name": provider_name})

            if provider_to_consumers or consumer_of_providers:
                return ContractConfig(
                    pactflow_name=lookup_name,
                    consumer_of=consumer_of_providers,
                    provider_to=provider_to_consumers,
                    source="pactflow_api",
                )

        except Exception as e:
            print(f"[ConfigReader] WARN: Pactflow API query failed: {e}")

        return None

    def _fallback_name(self) -> str:
        """Derive name from repo_name or repo_path."""
        if self.repo_name:
            # Strip org prefix if present (e.g. "KalpitaG/pact-provider-demo")
            return self.repo_name.split("/")[-1]
        if self.repo_path:
            return self.repo_path.name
        return ""


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def resolve_contract_config(
    repo_path: Optional[str] = None,
    repo_name: Optional[str] = None,
    pactflow_base_url: Optional[str] = None,
    pactflow_token: Optional[str] = None,
) -> ContractConfig:
    """
    Shorthand for ContractConfigReader(...).resolve().

    Returns a ContractConfig with pactflow_name, consumer_of, provider_to.
    """
    return ContractConfigReader(
        repo_path=repo_path,
        repo_name=repo_name,
        pactflow_base_url=pactflow_base_url,
        pactflow_token=pactflow_token,
    ).resolve()
