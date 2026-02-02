"""
Pactflow Context Collector
==========================
Fetches existing contract information from Pactflow/Pact Broker.
"""

import os
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
import requests
from langfuse import observe, get_client

load_dotenv()


@dataclass
class ContractInfo:
    """Information about a single contract."""
    consumer: str
    provider: str
    version: str
    created_at: Optional[str] = None
    verification_status: Optional[str] = None


@dataclass
class PactflowContext:
    """Structured container for Pactflow information."""
    broker_url: str
    pacticipants: list[dict] = field(default_factory=list)
    contracts: list[ContractInfo] = field(default_factory=list)

    def format_for_ai(self) -> str:
        """Format Pactflow context into a string optimized for AI consumption."""
        sections = []
        
        # Header
        sections.append(f"## Pactflow Contract Information")
        sections.append(f"**Broker URL:** {self.broker_url}")
        
        # Pacticipants (services)
        if self.pacticipants:
            sections.append(f"\n### Registered Services ({len(self.pacticipants)})")
            for p in self.pacticipants:
                version_info = f" (v{p['latest_version']})" if p.get('latest_version') else ""
                sections.append(f"- {p['name']}{version_info}")
        
        # Existing contracts
        if self.contracts:
            sections.append(f"\n### Existing Contracts ({len(self.contracts)})")
            
            # Group by verification status
            verified = [c for c in self.contracts if c.verification_status == "verified"]
            failed = [c for c in self.contracts if c.verification_status == "failed"]
            unverified = [c for c in self.contracts if c.verification_status == "unverified"]
            
            if verified:
                sections.append("\n**Verified:**")
                for c in verified:
                    sections.append(f"- {c.consumer} -> {c.provider} (v{c.version})")
            
            if failed:
                sections.append("\n**Failed:**")
                for c in failed:
                    sections.append(f"- {c.consumer} -> {c.provider} (v{c.version})")
            
            if unverified:
                sections.append("\n**Unverified:**")
                for c in unverified:
                    sections.append(f"- {c.consumer} -> {c.provider} (v{c.version})")
        else:
            sections.append("\n### Existing Contracts")
            sections.append("No contracts found.")
        
        formatted = "\n".join(sections)
        return formatted


class PactflowCollector:
    """
    Collects contract information from Pactflow.
    
    Usage:
        collector = PactflowCollector()
        context = collector.collect()
        formatted = context.format_for_ai()
    """
    
    def __init__(self):
        """Initialize Pactflow client with credentials from environment."""
        self.base_url = os.getenv("PACTFLOW_BASE_URL")
        self.token = os.getenv("PACTFLOW_TOKEN")
        self.timeout = int(os.getenv("API_TIMEOUT_SECONDS", "30"))
        
        if not all([self.base_url, self.token]):
            missing = []
            if not self.base_url: missing.append("PACTFLOW_BASE_URL")
            if not self.token: missing.append("PACTFLOW_TOKEN")
            raise ValueError(f"Missing Pactflow credentials: {', '.join(missing)}")
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/hal+json"
        }
    
    @observe(name="pactflow_collect")
    def collect(
        self, 
        consumer: Optional[str] = None, 
        provider: Optional[str] = None
    ) -> PactflowContext:
        """
        Collect contract information from Pactflow.
        
        Args:
            consumer: Filter by consumer name (optional)
            provider: Filter by provider name (optional)
            
        Returns:
            PactflowContext object with contract information
        """
        print(f"[Pactflow] Fetching contract data...")
        
        try:
            get_client().update_current_span(
                input={"consumer": consumer, "provider": provider}
            )
        except Exception:
            pass
        
        # Get all pacticipants (services)
        pacticipants = self._get_pacticipants()
        
        # Get contracts
        if provider:
            contracts = self._get_provider_contracts(provider)
        elif consumer:
            contracts = self._get_consumer_contracts(consumer)
        else:
            contracts = self._get_all_contracts(pacticipants)
        
        context = PactflowContext(
            broker_url=self.base_url,
            pacticipants=pacticipants,
            contracts=contracts
        )
        
        try:
            get_client().update_current_span(
                output={
                    "pacticipants_count": len(pacticipants),
                    "contracts_count": len(contracts)
                }
            )
        except Exception:
            pass
        
        print(f"  [OK] Found {len(pacticipants)} services, {len(contracts)} contracts")
        return context
    
    def _get_pacticipants(self) -> list[dict]:
        """Get all pacticipants (services) in the broker."""
        try:
            response = requests.get(
                f"{self.base_url}/pacticipants",
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            pacticipants = data.get("_embedded", {}).get("pacticipants", [])
            
            return [
                {
                    "name": p.get("name"),
                    "display_name": p.get("displayName"),
                    "latest_version": p.get("_embedded", {}).get("latestVersion", {}).get("number")
                }
                for p in pacticipants
            ]
        except Exception as e:
            print(f"  [WARN] Could not fetch pacticipants: {e}")
            return []
    
    def _get_all_contracts(self, pacticipants: list[dict]) -> list[ContractInfo]:
        """Get all contracts using the /pacts/latest endpoint."""
        contracts = []
        
        try:
            response = requests.get(
                f"{self.base_url}/pacts/latest",
                headers=self.headers,
                timeout=self.timeout
            )
            
            if response.status_code == 404:
                return []
            
            response.raise_for_status()
            data = response.json()
            
            # Get pacts from response
            pacts = data.get("pacts", [])
            
            for pact in pacts:
                embedded = pact.get("_embedded", {})
                consumer_info = embedded.get("consumer", {})
                provider_info = embedded.get("provider", {})
                
                consumer_name = consumer_info.get("name", "")
                provider_name = provider_info.get("name", "")
                
                # Get version info
                version_info = consumer_info.get("_embedded", {}).get("version", {})
                version = version_info.get("number", "unknown")
                
                created_at = pact.get("createdAt")
                
                if consumer_name and provider_name:
                    contracts.append(ContractInfo(
                        consumer=consumer_name,
                        provider=provider_name,
                        version=version,
                        created_at=created_at,
                        verification_status=self._get_verification_status(pact)
                    ))
                    
        except Exception as e:
            print(f"  [WARN] Could not fetch contracts: {e}")
        
        return contracts
    
    def _get_provider_contracts(self, provider: str) -> list[ContractInfo]:
        """Get all contracts where this service is the provider."""
        contracts = []
        
        try:
            response = requests.get(
                f"{self.base_url}/pacts/provider/{provider}/latest",
                headers=self.headers,
                timeout=self.timeout
            )
            
            if response.status_code == 404:
                return []
            
            response.raise_for_status()
            data = response.json()
            
            # Get pacts from response
            pacts = data.get("_embedded", {}).get("pacts", [])
            
            for pact in pacts:
                consumer_name = pact.get("_embedded", {}).get("consumer", {}).get("name", "")
                
                # Get version info
                version_info = pact.get("_embedded", {}).get("consumer", {}).get("_embedded", {}).get("version", {})
                version = version_info.get("number", "unknown")
                
                # Get verification status
                verification_status = self._get_verification_status(pact)
                
                if consumer_name:
                    contracts.append(ContractInfo(
                        consumer=consumer_name,
                        provider=provider,
                        version=version,
                        verification_status=verification_status
                    ))
                    
        except Exception as e:
            print(f"  [WARN] Could not fetch contracts for provider {provider}: {e}")
        
        return contracts
    
    def _get_consumer_contracts(self, consumer: str) -> list[ContractInfo]:
        """Get all contracts where this service is the consumer."""
        contracts = []
        
        # We need to check all providers to find ones that have this consumer
        pacticipants = self._get_pacticipants()
        
        for pacticipant in pacticipants:
            provider_name = pacticipant.get("name")
            if not provider_name or provider_name == consumer:
                continue
            
            provider_contracts = self._get_provider_contracts(provider_name)
            
            for contract in provider_contracts:
                if contract.consumer == consumer:
                    contracts.append(contract)
        
        return contracts
    
    def _get_verification_status(self, pact: dict) -> str:
        """Extract verification status from pact data."""
        # Try different paths for verification result
        verification = pact.get("_embedded", {}).get("latestVerificationResult", {})
        
        if not verification:
            verification = pact.get("latestVerificationResult", {})
        
        if not verification:
            return "unverified"
        
        success = verification.get("success")
        if success is True:
            return "verified"
        elif success is False:
            return "failed"
        else:
            return "unverified"