"""Export a recommendation as a portable AgentPGO YAML document."""
from typing import Any
from .staged import Candidate
def _payload(candidate:Candidate,include_metrics:bool)->dict[str,Any]:
    spec:dict[str,Any]={"id":candidate.id,"config":candidate.config}
    if include_metrics: spec["metrics"]={"quality":candidate.quality,"cost_usd":candidate.cost_usd,"latency_ms":candidate.latency_ms}
    return {"apiVersion":"agentpgo/v1","kind":"Recommendation","metadata":{"name":candidate.id},"spec":spec}
def export_yaml(candidate:Candidate,*,include_metrics:bool=True)->str:
    payload=_payload(candidate,include_metrics)
    try:
        import yaml
        return yaml.safe_dump(payload,sort_keys=False,allow_unicode=True)
    except ImportError:
        lines=["apiVersion: agentpgo/v1","kind: Recommendation","metadata:",f"  name: {candidate.id}","spec:",f"  id: {candidate.id}","  config:"]
        if candidate.config:
            lines.extend(f"    {key}: {value}" for key,value in candidate.config.items())
        else: lines.append("    {}")
        if include_metrics: lines.extend(["  metrics:",f"    quality: {candidate.quality}",f"    cost_usd: {candidate.cost_usd}",f"    latency_ms: {candidate.latency_ms}"])
        return "\n".join(lines)+"\n"
