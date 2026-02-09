"""
Role Detector Module
====================

Determines service role (consumer/provider/both) using a hierarchical approach:
1. Service Registry (fastest, most reliable)
2. Pact Broker (existing relationships)
3. AI Analysis (for new services/relationships)

Usage:
    from role_detector import RoleDetector
    
    detector = RoleDetector()
    result = detector.detect_role(
        service_name="email-service",
        pr_files=["src/clients/api.ts", "src/routes/index.ts"],
        pr_diff="... diff content ..."
    )
    
    print(result)
    # {
    #     "is_consumer": True,
    #     "is_provider": False,
    #     "consumer_of": ["email-content-convertor"],
    #     "provider_to": [],
    #     "source": "registry",
    #     "confidence": 1.0
    # }
"""

import os
import yaml
import requests
from dataclasses import dataclass
from typing import Optional
from langfuse.decorators import observe
from google import genai


@dataclass
class RoleDetectionResult:
    """Result of role detection."""
    is_consumer: bool
    is_provider: bool
    consumer_of: list
    provider_to: list
    source: str  # "registry", "broker", "ai", "fallback"
    confidence: float
    evidence: Optional[str] = None


class RoleDetector:
    """
    Detects whether a service is a consumer, provider, or both.
    
    Uses a hierarchical detection approach:
    1. Check service registry (cached/manual config)
    2. Query Pact Broker (existing pacts)
    3. AI analysis (for new services)
    """
    
    def __init__(
        self,
        registry_path: str = "config/service-registry.yml",
        pactflow_url: Optional[str] = None,
        pactflow_token: Optional[str] = None,
        gemini_api_key: Optional[str] = None
    ):
        self.registry_path = registry_path
        self.pactflow_url = pactflow_url or os.getenv("PACTFLOW_BASE_URL")
        self.pactflow_token = pactflow_token or os.getenv("PACTFLOW_TOKEN")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        
        # Load registry
        self.registry = self._load_registry()
        
        # Initialize Gemini client for AI detection
        if self.gemini_api_key:
            self.genai_client = genai.Client(api_key=self.gemini_api_key)
    
    def _load_registry(self) -> dict:
        """Load the service registry from YAML file."""
        if os.path.exists(self.registry_path):
            with open(self.registry_path, 'r') as f:
                return yaml.safe_load(f) or {"services": {}}
        return {"services": {}}
    
    def _save_registry(self):
        """Save the service registry to YAML file."""
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        with open(self.registry_path, 'w') as f:
            yaml.dump(self.registry, f, default_flow_style=False, sort_keys=False)
    
    @observe(name="detect_role")
    def detect_role(
        self,
        service_name: str,
        pr_files: list = None,
        pr_diff: str = None,
        force_ai: bool = False
    ) -> RoleDetectionResult:
        """
        Detect the role of a service.
        
        Args:
            service_name: Name of the service (repo name)
            pr_files: List of files changed in the PR
            pr_diff: Git diff content
            force_ai: Force AI detection even if found in registry
        
        Returns:
            RoleDetectionResult with role information
        """
        print(f"\n🔍 Detecting role for: {service_name}")
        
        # Step 1: Check registry (unless force_ai)
        if not force_ai:
            result = self._check_registry(service_name)
            if result:
                print(f"  ✅ Found in registry: consumer={result.is_consumer}, provider={result.is_provider}")
                return result
        
        # Step 2: Query Pact Broker
        result = self._check_pact_broker(service_name)
        if result and (result.is_consumer or result.is_provider):
            print(f"  ✅ Found in Pact Broker: consumer={result.is_consumer}, provider={result.is_provider}")
            # Update registry with broker findings
            self._update_registry(service_name, result)
            return result
        
        # Step 3: AI Analysis (if we have PR context)
        if pr_files or pr_diff:
            result = self._ai_detect(service_name, pr_files, pr_diff)
            if result:
                print(f"  ✅ AI detected: consumer={result.is_consumer}, provider={result.is_provider}")
                # Update registry with AI findings
                self._update_registry(service_name, result)
                return result
        
        # Step 4: Fallback - ask for clarification
        print(f"  ⚠️  Could not determine role for {service_name}")
        return RoleDetectionResult(
            is_consumer=False,
            is_provider=False,
            consumer_of=[],
            provider_to=[],
            source="fallback",
            confidence=0.0,
            evidence="Could not detect role - no registry entry, no broker pacts, no PR context"
        )
    
    @observe(name="check_registry")
    def _check_registry(self, service_name: str) -> Optional[RoleDetectionResult]:
        """Check if service exists in the registry."""
        services = self.registry.get("services", {})
        service = services.get(service_name)
        
        if not service:
            return None
        
        consumer_of = service.get("consumer_of", [])
        provider_to = service.get("provider_to", [])
        detection = service.get("detection", {})
        
        return RoleDetectionResult(
            is_consumer=len(consumer_of) > 0,
            is_provider=len(provider_to) > 0,
            consumer_of=consumer_of,
            provider_to=provider_to,
            source="registry",
            confidence=detection.get("confidence", 1.0),
            evidence=detection.get("evidence")
        )
    
    @observe(name="check_pact_broker")
    def _check_pact_broker(self, service_name: str) -> Optional[RoleDetectionResult]:
        """Query Pact Broker for existing relationships."""
        if not self.pactflow_url or not self.pactflow_token:
            print("  ⚠️  Pact Broker credentials not configured")
            return None
        
        headers = {
            "Authorization": f"Bearer {self.pactflow_token}",
            "Accept": "application/hal+json"
        }
        
        consumer_of = []
        provider_to = []
        
        try:
            # Check if this service is a PROVIDER (has consumers)
            provider_url = f"{self.pactflow_url}/pacts/provider/{service_name}/latest"
            response = requests.get(provider_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                # Extract consumer names from pact links
                pacts = data.get("_links", {}).get("pb:pacts", [])
                for pact in pacts:
                    # Parse consumer name from pact link
                    name = pact.get("name", "")
                    if name:
                        provider_to.append(name.split("/")[0] if "/" in name else name)
            
            # Check if this service is a CONSUMER (has providers)
            # We need to check against known providers or all pacts
            # For now, we check if any pacts exist where this is the consumer
            consumer_url = f"{self.pactflow_url}/pacts/latest"
            response = requests.get(consumer_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                pacts = data.get("pacts", [])
                for pact in pacts:
                    consumer = pact.get("_embedded", {}).get("consumer", {}).get("name", "")
                    provider = pact.get("_embedded", {}).get("provider", {}).get("name", "")
                    
                    if consumer == service_name:
                        consumer_of.append(provider)
            
            if consumer_of or provider_to:
                return RoleDetectionResult(
                    is_consumer=len(consumer_of) > 0,
                    is_provider=len(provider_to) > 0,
                    consumer_of=list(set(consumer_of)),
                    provider_to=list(set(provider_to)),
                    source="broker",
                    confidence=1.0,
                    evidence=f"Found in Pact Broker: consumes {consumer_of}, provides to {provider_to}"
                )
        
        except requests.RequestException as e:
            print(f"  ⚠️  Pact Broker error: {e}")
        
        return None
    
    @observe(name="ai_detect_role")
    def _ai_detect(
        self,
        service_name: str,
        pr_files: list,
        pr_diff: str
    ) -> Optional[RoleDetectionResult]:
        """Use AI to detect service role from PR changes."""
        if not self.gemini_api_key:
            print("  ⚠️  Gemini API key not configured")
            return None
        
        # Build the prompt
        prompt = self._build_detection_prompt(service_name, pr_files, pr_diff)
        
        try:
            response = self.genai_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            
            # Parse AI response
            return self._parse_ai_response(response.text, service_name)
        
        except Exception as e:
            print(f"  ⚠️  AI detection error: {e}")
            return None
    
    def _build_detection_prompt(
        self,
        service_name: str,
        pr_files: list,
        pr_diff: str
    ) -> str:
        """Build the prompt for AI role detection."""
        return f"""You are analyzing a microservice to determine its role in contract testing.

SERVICE NAME: {service_name}

FILES CHANGED IN PR:
{chr(10).join(f'- {f}' for f in (pr_files or []))}

CODE CHANGES (DIFF):
```
{pr_diff[:5000] if pr_diff else "No diff provided"}
```

TASK: Determine if this service is a CONSUMER, PROVIDER, or BOTH.

DEFINITIONS:
- CONSUMER: Makes HTTP requests to OTHER services (has HTTP clients, API calls, fetch/axios usage)
- PROVIDER: Exposes HTTP endpoints that OTHER services call (has routes, controllers, API handlers)

INDICATORS:
Consumer indicators:
- Files like: **/clients/**, **/services/**, **/*Client.ts, **/*Api.ts
- Imports: axios, fetch, http-client, request libraries
- Code patterns: httpClient.get(), api.post(), fetch(), axios()

Provider indicators:
- Files like: **/routes/**, **/controllers/**, **/handlers/**, **/api/**
- Imports: express, fastify, flask, spring controllers
- Code patterns: app.get(), router.post(), @GetMapping, @Controller

RESPONSE FORMAT (JSON only, no markdown):
{{
  "is_consumer": true/false,
  "is_provider": true/false,
  "consumer_of": ["service-name-1", "service-name-2"],
  "provider_to": ["service-name-1"],
  "confidence": 0.0-1.0,
  "evidence": "Brief explanation of what indicated this"
}}

RULES:
1. If you see HTTP client code making external calls → is_consumer = true
2. If you see route/controller code exposing endpoints → is_provider = true
3. A service CAN be both consumer and provider
4. If you can identify specific service names being called, list them in consumer_of
5. If unsure about specific names, use ["unknown-provider"] 
6. Confidence should reflect how certain you are (0.5 = guess, 0.9 = confident)

Respond with ONLY the JSON object, no explanation."""
    
    def _parse_ai_response(
        self,
        response_text: str,
        service_name: str
    ) -> Optional[RoleDetectionResult]:
        """Parse the AI response into a RoleDetectionResult."""
        import json
        
        try:
            # Clean up response (remove markdown code blocks if present)
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            
            data = json.loads(text)
            
            return RoleDetectionResult(
                is_consumer=data.get("is_consumer", False),
                is_provider=data.get("is_provider", False),
                consumer_of=data.get("consumer_of", []),
                provider_to=data.get("provider_to", []),
                source="ai",
                confidence=data.get("confidence", 0.5),
                evidence=data.get("evidence", "AI analysis")
            )
        
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ⚠️  Failed to parse AI response: {e}")
            return None
    
    def _update_registry(self, service_name: str, result: RoleDetectionResult):
        """Update the registry with detection results."""
        from datetime import datetime
        
        if "services" not in self.registry:
            self.registry["services"] = {}
        
        self.registry["services"][service_name] = {
            "consumer_of": result.consumer_of,
            "provider_to": result.provider_to,
            "detection": {
                "detected_at": datetime.utcnow().isoformat() + "Z",
                "confidence": result.confidence,
                "method": result.source,
                "evidence": result.evidence
            }
        }
        
        self._save_registry()
        print(f"  📝 Updated registry for {service_name}")


# Convenience function for use in pipeline
def detect_service_role(
    service_name: str,
    pr_files: list = None,
    pr_diff: str = None
) -> dict:
    """
    Convenience function to detect service role.
    
    Returns dict for easy use in workflow:
    {
        "run_consumer_workflow": bool,
        "run_provider_workflow": bool,
        "consumer_of": list,
        "provider_to": list,
        "detection_source": str
    }
    """
    detector = RoleDetector()
    result = detector.detect_role(service_name, pr_files, pr_diff)
    
    return {
        "run_consumer_workflow": result.is_consumer,
        "run_provider_workflow": result.is_provider,
        "consumer_of": result.consumer_of,
        "provider_to": result.provider_to,
        "detection_source": result.source,
        "confidence": result.confidence
    }


if __name__ == "__main__":
    # Test the detector
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python role_detector.py <service-name>")
        sys.exit(1)
    
    service = sys.argv[1]
    result = detect_service_role(service)
    print(f"\nResult for {service}:")
    print(f"  Run consumer workflow: {result['run_consumer_workflow']}")
    print(f"  Run provider workflow: {result['run_provider_workflow']}")
    print(f"  Consumer of: {result['consumer_of']}")
    print(f"  Provider to: {result['provider_to']}")
    print(f"  Detection source: {result['detection_source']}")
