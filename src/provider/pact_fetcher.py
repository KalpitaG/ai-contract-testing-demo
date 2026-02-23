"""
Pact Fetcher Module
===================

Fetches pacts from Pact Broker/Pactflow and extracts provider states
and interaction details needed for AI generation.

Usage:
    from src.provider.pact_fetcher import PactFetcher
    
    fetcher = PactFetcher()
    pact_context = fetcher.fetch_provider_pacts("ProviderService")
    
    print(pact_context.provider_states)
    # ['item with id 1 exists', 'no items exist', ...]
"""

import os
import requests
from dataclasses import dataclass, field
from typing import Optional
from langfuse.decorators import observe


@dataclass
class PactInteraction:
    """Single interaction from a pact."""
    provider_state: str
    description: str
    request_method: str
    request_path: str
    request_headers: dict = field(default_factory=dict)
    request_body: Optional[dict] = None
    request_query: Optional[str] = None
    response_status: int = 200
    response_headers: dict = field(default_factory=dict)
    response_body: Optional[dict] = None


@dataclass
class PactContext:
    """Context extracted from pacts for a provider."""
    provider_name: str
    consumers: list
    provider_states: list
    interactions: list
    raw_pacts: list = field(default_factory=list)
    
    def format_for_ai(self) -> str:
        """Format pact context for AI prompt."""
        output = []
        output.append(f"# Pact Context for Provider: {self.provider_name}")
        output.append(f"\n## Consumers: {', '.join(self.consumers)}")
        
        output.append("\n## Provider States Required:")
        for state in self.provider_states:
            output.append(f"  - \"{state}\"")
        
        output.append("\n## Interactions:")
        for idx, interaction in enumerate(self.interactions, 1):
            output.append(f"\n### Interaction {idx}: {interaction.description}")
            output.append(f"  Provider State: \"{interaction.provider_state}\"")
            output.append(f"  Request: {interaction.request_method} {interaction.request_path}")
            if interaction.request_query:
                output.append(f"  Query: {interaction.request_query}")
            if interaction.request_body:
                output.append(f"  Request Body: {interaction.request_body}")
            output.append(f"  Expected Response Status: {interaction.response_status}")
            if interaction.response_body:
                output.append(f"  Expected Response Body: {interaction.response_body}")
        
        return "\n".join(output)


class PactFetcher:
    """
    Fetches pacts from Pact Broker/Pactflow.
    
    Extracts:
    - Provider states that need state handlers
    - Interactions (request/response pairs)
    - Consumer information
    """
    
    def __init__(
        self,
        broker_url: Optional[str] = None,
        broker_token: Optional[str] = None
    ):
        self.broker_url = (broker_url or os.getenv("PACTFLOW_BASE_URL", "")).rstrip("/")
        self.broker_token = broker_token or os.getenv("PACTFLOW_TOKEN")
        
        if not self.broker_url:
            raise ValueError("Pact Broker URL not configured. Set PACTFLOW_BASE_URL.")
    
    def _get_headers(self) -> dict:
        """Get headers for broker API calls."""
        headers = {
            "Accept": "application/hal+json, application/json"
        }
        if self.broker_token:
            headers["Authorization"] = f"Bearer {self.broker_token}"
        return headers
    
    @observe(name="fetch_provider_pacts")
    def fetch_provider_pacts(
        self, 
        provider_name: str,
        pact_url: Optional[str] = None 
        ) -> PactContext:
        """
        Fetch pacts for a provider and extract context.
        If pact_url is provided (webhook trigger), fetches only that specific pact.
        Otherwise fetches all latest pacts for the provider.
        
        Args:
            provider_name: Name of the provider service
            pact_url: Optional specific pact URL from Pactflow webhook
            
        Returns:
            PactContext with all extracted information
        """
        # If specific pact URL provided (webhook trigger), fetch just that one
        if pact_url:
            print(f"\n📥 Fetching specific pact from webhook: {pact_url}")
            pact_data = self._fetch_single_pact(pact_url)
            if not pact_data:
                return PactContext(
                    provider_name=provider_name,
                    consumers=[],
                    provider_states=[],
                    interactions=[],
                    raw_pacts=[]
                )
            # Parse the single pact directly
            consumers = [pact_data.get("consumer", {}).get("name", "Unknown")]
            all_interactions = [
                self._parse_interaction(i)
                for i in pact_data.get("interactions", [])
            ]
            all_states = {i.provider_state for i in all_interactions if i.provider_state}
            return PactContext(
                provider_name=provider_name,
                consumers=consumers,
                provider_states=sorted(list(all_states)),
                interactions=all_interactions,
                raw_pacts=[pact_data]
            )

        # Otherwise fetch all latest pacts (push/label trigger)
        print(f"\n📥 Fetching all latest pacts for provider: {provider_name}")
        pacts_url = f"{self.broker_url}/pacts/provider/{provider_name}/latest"
        
        try:
            response = requests.get(
                pacts_url,
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 404:
                print(f"  ⚠️  No pacts found for provider: {provider_name}")
                return PactContext(
                    provider_name=provider_name,
                    consumers=[],
                    provider_states=[],
                    interactions=[],
                    raw_pacts=[]
                )
            
            response.raise_for_status()
            pacts_data = response.json()
            
        except requests.RequestException as e:
            print(f"  ❌ Error fetching pacts: {e}")
            raise
        
        # Extract pact links
        pact_links = pacts_data.get("_links", {}).get("pb:pacts", [])
        if not pact_links:
            # Try alternative structure
            pact_links = pacts_data.get("_links", {}).get("pacts", [])
        
        if not pact_links:
            print(f"  ⚠️  No pact links found in response")
            return PactContext(
                provider_name=provider_name,
                consumers=[],
                provider_states=[],
                interactions=[],
                raw_pacts=[]
            )
        
        # Fetch each pact
        consumers = []
        all_states = set()
        all_interactions = []
        raw_pacts = []
        
        for pact_link in pact_links:
            pact_url = pact_link.get("href")
            if not pact_url:
                continue
            
            pact_data = self._fetch_single_pact(pact_url)
            if pact_data:
                raw_pacts.append(pact_data)
                
                # Extract consumer name
                consumer_name = pact_data.get("consumer", {}).get("name", "Unknown")
                if consumer_name not in consumers:
                    consumers.append(consumer_name)
                
                # Extract interactions
                for interaction in pact_data.get("interactions", []):
                    parsed = self._parse_interaction(interaction)
                    all_interactions.append(parsed)
                    
                    if parsed.provider_state:
                        all_states.add(parsed.provider_state)
        
        provider_states = sorted(list(all_states))
        
        print(f"  ✅ Found {len(consumers)} consumer(s): {consumers}")
        print(f"  ✅ Found {len(provider_states)} provider state(s)")
        print(f"  ✅ Found {len(all_interactions)} interaction(s)")
        
        return PactContext(
            provider_name=provider_name,
            consumers=consumers,
            provider_states=provider_states,
            interactions=all_interactions,
            raw_pacts=raw_pacts
        )
    
    def _fetch_single_pact(self, pact_url: str) -> Optional[dict]:
        """Fetch a single pact by URL."""
        try:
            response = requests.get(
                pact_url,
                headers=self._get_headers(),
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"  ⚠️  Error fetching pact {pact_url}: {e}")
            return None
    
    def _parse_interaction(self, interaction: dict) -> PactInteraction:
        """Parse a single interaction from pact JSON."""
        # Handle provider state (can be string or list)
        provider_state = ""
        if "providerState" in interaction:
            provider_state = interaction["providerState"]
        elif "providerStates" in interaction:
            states = interaction["providerStates"]
            if states:
                provider_state = states[0].get("name", "")
        elif "provider_state" in interaction:
            provider_state = interaction["provider_state"]
        elif "provider_states" in interaction:
            states = interaction["provider_states"]
            if states:
                provider_state = states[0].get("name", "")
        
        request = interaction.get("request", {})
        response = interaction.get("response", {})
        
        return PactInteraction(
            provider_state=provider_state,
            description=interaction.get("description", "Unknown interaction"),
            request_method=request.get("method", "GET"),
            request_path=request.get("path", "/"),
            request_headers=request.get("headers", {}),
            request_body=request.get("body"),
            request_query=request.get("query"),
            response_status=response.get("status", 200),
            response_headers=response.get("headers", {}),
            response_body=response.get("body")
        )
    
    @observe(name="get_provider_states_summary")
    def get_provider_states_summary(self, provider_name: str) -> dict:
        """
        Get a summary of provider states and what they need.
        
        Returns dict mapping state name to expected data.
        """
        context = self.fetch_provider_pacts(provider_name)
        
        states_summary = {}
        
        for interaction in context.interactions:
            state = interaction.provider_state
            if not state:
                continue
            
            if state not in states_summary:
                states_summary[state] = {
                    "interactions": [],
                    "expected_data": []
                }
            
            states_summary[state]["interactions"].append({
                "description": interaction.description,
                "method": interaction.request_method,
                "path": interaction.request_path,
                "response_status": interaction.response_status,
                "response_body": interaction.response_body
            })
            
            # Extract expected data from response body
            if interaction.response_body:
                states_summary[state]["expected_data"].append(interaction.response_body)
        
        return states_summary


# Convenience function
def fetch_pact_context(provider_name: str) -> PactContext:
    """Fetch pact context for a provider."""
    fetcher = PactFetcher()
    return fetcher.fetch_provider_pacts(provider_name)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m src.provider.pact_fetcher <provider-name>")
        sys.exit(1)
    
    provider = sys.argv[1]
    context = fetch_pact_context(provider)
    
    print("\n" + "="*60)
    print(context.format_for_ai())
