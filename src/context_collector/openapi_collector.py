"""
OpenAPI Context Collector
=========================
Parses and structures OpenAPI/Swagger specifications for AI consumption.

Supports:
- OpenAPI 3.x (JSON and YAML)
- Swagger 2.x (JSON and YAML)
"""

import os
import json
from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv
import yaml
from langfuse import observe, get_client

load_dotenv()


@dataclass
class EndpointInfo:
    """Information about a single API endpoint."""
    path: str
    method: str
    operation_id: Optional[str]
    summary: Optional[str]
    description: Optional[str]
    parameters: list[dict] = field(default_factory=list)
    request_body: Optional[dict] = None
    responses: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class OpenAPIContext:
    """Structured container for OpenAPI specification."""
    title: str
    version: str
    description: Optional[str]
    base_url: Optional[str]
    spec_version: str  # "openapi" or "swagger"
    endpoints: list[EndpointInfo] = field(default_factory=list)
    schemas: dict = field(default_factory=dict)
    raw_spec: dict = field(default_factory=dict)

    def format_for_ai(self) -> str:
        """Format OpenAPI context into a string optimized for AI consumption."""
        sections = []
        
        # Header
        sections.append(f"## API Specification: {self.title}")
        sections.append(f"**Version:** {self.version}")
        sections.append(f"**Spec Format:** {self.spec_version.upper()}")
        
        if self.base_url:
            sections.append(f"**Base URL:** {self.base_url}")
        
        # Description
        if self.description:
            desc = self.description[:500] if len(self.description) > 500 else self.description
            sections.append(f"\n### Description\n{desc}")
        
        # Endpoints
        sections.append(f"\n### Endpoints ({len(self.endpoints)} total)")
        
        for endpoint in self.endpoints:
            sections.append(f"\n#### {endpoint.method} {endpoint.path}")
            
            if endpoint.summary:
                sections.append(f"**Summary:** {endpoint.summary}")
            
            if endpoint.operation_id:
                sections.append(f"**Operation ID:** {endpoint.operation_id}")
            
            # Parameters
            if endpoint.parameters:
                sections.append("\n**Parameters:**")
                for param in endpoint.parameters:
                    required = " (required)" if param.get("required") else ""
                    sections.append(f"- `{param['name']}` ({param['in']}, {param['type']}){required}")
            
            # Request Body
            if endpoint.request_body:
                sections.append("\n**Request Body:**")
                sections.append(f"```json\n{json.dumps(endpoint.request_body, indent=2)}\n```")
            
            # Responses
            if endpoint.responses:
                sections.append("\n**Responses:**")
                for status, response in endpoint.responses.items():
                    desc = response.get("description", "")
                    sections.append(f"- `{status}`: {desc}")
        
        # Schemas (limited to avoid token explosion)
        if self.schemas:
            sections.append(f"\n### Data Schemas ({len(self.schemas)} total)")
            for name, schema in list(self.schemas.items())[:5]:  # Limit to 5
                sections.append(f"\n#### {name}")
                schema_str = json.dumps(schema, indent=2)
                if len(schema_str) > 500:
                    schema_str = schema_str[:500] + "\n... [truncated]"
                sections.append(f"```json\n{schema_str}\n```")
        
        formatted = "\n".join(sections)
        return formatted


class OpenAPICollector:
    """
    Parses OpenAPI specifications for AI consumption.
    
    Usage:
        collector = OpenAPICollector()
        context = collector.collect_from_file("openapi.yaml")
        # or
        context = collector.collect_from_dict(spec_dict)
        formatted = context.format_for_ai()
    """
    
    @observe(name="openapi_collect")
    def collect_from_file(self, file_path: str) -> OpenAPIContext:
        """
        Collect OpenAPI context from a file.
        
        Args:
            file_path: Path to OpenAPI spec (JSON or YAML)
            
        Returns:
            OpenAPIContext object with parsed specification
        """
        print(f"[OpenAPI] Parsing spec: {file_path}")
        
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"OpenAPI spec not found: {file_path}")
        
        # Read and parse file
        content = path.read_text()
        
        if path.suffix in ['.yaml', '.yml']:
            spec = yaml.safe_load(content)
        else:
            spec = json.loads(content)
        
        return self.collect_from_dict(spec, source=file_path)
    
    @observe(name="openapi_collect")
    def collect_from_dict(self, spec: dict, source: str = "dict") -> OpenAPIContext:
        """
        Collect OpenAPI context from a dictionary.
        
        Args:
            spec: OpenAPI specification as dictionary
            source: Source identifier for logging
            
        Returns:
            OpenAPIContext object with parsed specification
        """
        try:
            get_client().update_current_span(
                input={"source": source}
            )
        except Exception:
            pass
        
        # Detect spec version
        spec_version = "openapi" if "openapi" in spec else "swagger"
        
        # Extract basic info
        info = spec.get("info", {})
        title = info.get("title", "Unknown API")
        version = info.get("version", "unknown")
        description = info.get("description")
        
        # Extract base URL
        base_url = self._extract_base_url(spec, spec_version)
        
        # Extract endpoints
        endpoints = self._extract_endpoints(spec, spec_version)
        
        # Extract schemas
        schemas = self._extract_schemas(spec, spec_version)
        
        context = OpenAPIContext(
            title=title,
            version=version,
            description=description,
            base_url=base_url,
            spec_version=spec_version,
            endpoints=endpoints,
            schemas=schemas,
            raw_spec=spec
        )
        
        try:
            get_client().update_current_span(
                output={
                    "title": title,
                    "version": version,
                    "spec_version": spec_version,
                    "endpoints_count": len(endpoints),
                    "schemas_count": len(schemas)
                }
            )
        except Exception:
            pass
        
        print(f"  [OK] Parsed: {title} v{version} ({len(endpoints)} endpoints)")
        return context
    
    def _extract_base_url(self, spec: dict, spec_version: str) -> Optional[str]:
        """Extract base URL from spec."""
        if spec_version == "openapi":
            # OpenAPI 3.x uses servers array
            servers = spec.get("servers", [])
            if servers:
                return servers[0].get("url")
        else:
            # Swagger 2.x uses host + basePath
            host = spec.get("host", "")
            base_path = spec.get("basePath", "")
            schemes = spec.get("schemes", ["https"])
            if host:
                return f"{schemes[0]}://{host}{base_path}"
        return None
    
    def _extract_endpoints(self, spec: dict, spec_version: str) -> list[EndpointInfo]:
        """Extract all endpoints from the spec."""
        endpoints = []
        paths = spec.get("paths", {})
        
        for path, path_item in paths.items():
            # Skip path-level parameters
            if not isinstance(path_item, dict):
                continue
            
            for method in ["get", "post", "put", "patch", "delete", "options", "head"]:
                if method not in path_item:
                    continue
                
                operation = path_item[method]
                
                # Extract parameters
                parameters = self._extract_parameters(
                    operation.get("parameters", []),
                    path_item.get("parameters", [])  # Path-level params
                )
                
                # Extract request body (OpenAPI 3.x)
                request_body = None
                if spec_version == "openapi" and "requestBody" in operation:
                    request_body = self._simplify_request_body(operation["requestBody"])
                elif spec_version == "swagger":
                    # Swagger 2.x uses body parameter
                    body_params = [p for p in parameters if p.get("in") == "body"]
                    if body_params:
                        request_body = body_params[0].get("schema")
                
                # Extract responses
                responses = self._extract_responses(operation.get("responses", {}))
                
                endpoint = EndpointInfo(
                    path=path,
                    method=method.upper(),
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    description=operation.get("description"),
                    parameters=parameters,
                    request_body=request_body,
                    responses=responses,
                    tags=operation.get("tags", [])
                )
                
                endpoints.append(endpoint)
        
        return endpoints
    
    def _extract_parameters(self, operation_params: list, path_params: list) -> list[dict]:
        """Combine and simplify parameters."""
        all_params = path_params + operation_params
        simplified = []
        
        for param in all_params:
            simplified.append({
                "name": param.get("name"),
                "in": param.get("in"),  # path, query, header, cookie
                "required": param.get("required", False),
                "type": self._get_param_type(param),
                "description": param.get("description")
            })
        
        return simplified
    
    def _get_param_type(self, param: dict) -> str:
        """Extract parameter type from different spec versions."""
        # OpenAPI 3.x
        if "schema" in param:
            return param["schema"].get("type", "unknown")
        # Swagger 2.x
        return param.get("type", "unknown")
    
    def _simplify_request_body(self, request_body: dict) -> Optional[dict]:
        """Simplify request body for AI consumption."""
        content = request_body.get("content", {})
        
        # Prefer JSON content
        for content_type in ["application/json", "application/x-www-form-urlencoded"]:
            if content_type in content:
                schema = content[content_type].get("schema", {})
                return {
                    "content_type": content_type,
                    "required": request_body.get("required", False),
                    "schema": schema
                }
        
        # Return first available content type
        if content:
            first_type = list(content.keys())[0]
            return {
                "content_type": first_type,
                "required": request_body.get("required", False),
                "schema": content[first_type].get("schema", {})
            }
        
        return None
    
    def _extract_responses(self, responses: dict) -> dict:
        """Extract and simplify response definitions."""
        simplified = {}
        
        for status_code, response in responses.items():
            simplified[status_code] = {
                "description": response.get("description", ""),
                "schema": self._extract_response_schema(response)
            }
        
        return simplified
    
    def _extract_response_schema(self, response: dict) -> Optional[dict]:
        """Extract response schema from different spec versions."""
        # OpenAPI 3.x
        content = response.get("content", {})
        if content:
            for content_type in ["application/json", "application/xml"]:
                if content_type in content:
                    return content[content_type].get("schema")
        
        # Swagger 2.x
        return response.get("schema")
    
    def _extract_schemas(self, spec: dict, spec_version: str) -> dict:
        """Extract schema definitions from the spec."""
        if spec_version == "openapi":
            # OpenAPI 3.x
            return spec.get("components", {}).get("schemas", {})
        else:
            # Swagger 2.x
            return spec.get("definitions", {})