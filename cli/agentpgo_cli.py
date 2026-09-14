"""Small, dependency-free AgentPGO command surface.

The local mode is intentionally an artifact harness: it validates input and
stores reproducible hand-off files.  ``--remote`` forwards the same payloads
to the API when a backend is available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CONFIG_DIR = ".agentpgo"
CONFIG_FILE = "config.json"
DEFAULT_API_URL = "http://localhost:8000"


class CliError(Exception):
    """An expected user-facing CLI error."""


def _root(path: Path | None = None) -> Path:
    return (path or Path.cwd()).expanduser().resolve()


def _config_path(root: Path | None = None) -> Path:
    return _root(root) / CONFIG_DIR / CONFIG_FILE


def _read_config(root: Path | None = None) -> dict[str, Any]:
    path = _config_path(root)
    if not path.exists():
        raise CliError("No AgentPGO project found. Run `agentpgo init` first.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("project"), str):
        raise CliError(f"Invalid AgentPGO config at {path}")
    return value


def _write_json(root: Path, relative: str, value: dict[str, Any]) -> None:
    path = root / CONFIG_DIR / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_records(path_arg: str, key: str) -> list[dict[str, Any]]:
    path = Path(path_arg).expanduser()
    if not path.is_file():
        raise CliError(f"Input file does not exist: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"Could not read {path}: {exc}") from exc
    if not raw.strip():
        raise CliError(f"Input file is empty: {path}")

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CliError(
                    f"Invalid JSON on line {line_number} of {path}; expected valid JSON or JSONL"
                ) from exc
            if not isinstance(item, dict):
                raise CliError(f"Record on line {line_number} of {path} must be an object")
            records.append(item)
        if not records:
            raise CliError(f"Input must contain valid JSON or JSONL: {path}")
        return records

    if isinstance(decoded, dict) and key in decoded:
        decoded = decoded[key]
    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list) and all(isinstance(item, dict) for item in decoded):
        return decoded
    raise CliError(f"Input must contain an object or an array of objects: {path}")


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _api_url(config: dict[str, Any], override: str | None) -> str:
    return (override or os.environ.get("AGENTPGO_API_URL") or config.get("api_url") or DEFAULT_API_URL).rstrip("/")


def _request(config: dict[str, Any], override: str | None, method: str, path: str, payload: Any = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "agentpgo-cli/0.1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    api_key = os.environ.get("AGENTPGO_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(_api_url(config, override) + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CliError(f"API request failed ({exc.code}): {detail[:500]}") from exc
    except URLError as exc:
        raise CliError(f"Could not reach AgentPGO API at {_api_url(config, override)}: {exc.reason}") from exc
    if not response_body:
        return {"status": "ok"}
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return {"body": response_body}


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(Path(args.directory))
    config_path = _config_path(root)
    if config_path.exists() and not args.force:
        existing = _read_config(root)
        return {"status": "exists", "config_path": str(config_path), "project": existing["project"]}
    project = args.project or root.name
    config = {
        "schema_version": 1,
        "project": project,
        "api_url": args.api_url or DEFAULT_API_URL,
        "artifacts_dir": CONFIG_DIR,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "initialized", "config_path": str(config_path), "project": project}


def cmd_profile(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    config = _read_config(root)
    traces = _load_records(args.input, "traces")
    payload = {"project": config["project"], "traces": traces}
    if args.remote:
        result = _request(config, args.api_url, "POST", "/v1/profiles", payload)
        _print(result)
        return result if isinstance(result, dict) else {"result": result}
    result = {
        "schema_version": 1,
        "project": config["project"],
        "trace_count": len(traces),
        "traces": traces,
        "mode": "local-artifact",
    }
    _write_json(root, "profiles/latest.json", result)
    _print(result)
    return result


def cmd_eval_import(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    config = _read_config(root)
    cases = _load_records(args.input, "cases")
    payload = {"project": config["project"], "cases": cases}
    if args.remote:
        result = _request(config, args.api_url, "POST", "/v1/evals/import", payload)
        _print(result)
        return result if isinstance(result, dict) else {"result": result}
    result = {
        "schema_version": 1,
        "project": config["project"],
        "case_count": len(cases),
        "cases": cases,
        "mode": "local-artifact",
    }
    _write_json(root, "evals/latest.json", result)
    _print(result)
    return result


def cmd_optimize(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    config = _read_config(root)
    profile_path = root / CONFIG_DIR / "profiles/latest.json"
    if not profile_path.exists() and not args.remote:
        raise CliError("No profile found. Run `agentpgo profile <traces.jsonl>` first.")
    payload = {"project": config["project"]}
    if args.remote:
        result = _request(config, args.api_url, "POST", "/v1/optimize", payload)
        _print(result)
        return result if isinstance(result, dict) else {"result": result}
    result = {
        "schema_version": 1,
        "project": config["project"],
        "status": "ready",
        "mode": "local-artifact",
        "profile": "profiles/latest.json",
        "note": "Hand-off artifact only; run with --remote for backend optimization.",
    }
    _write_json(root, "policy.json", result)
    _print(result)
    return result


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or any(char in text for char in ":#{}[]&,*!|>'\"%@`\n") or text.lower() in {"true", "false", "null"}:
        return json.dumps(text)
    return text


def _yaml_mapping(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f"{key}:")
            nested = _yaml_mapping(item)
            lines.extend(f"  {line}" for line in nested.splitlines())
        elif isinstance(item, list):
            lines.append(f"{key}:")
            for entry in item:
                lines.append(f"  - {_yaml_scalar(entry)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(item)}")
    return "\n".join(lines) + "\n"


def cmd_export(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    config = _read_config(root)
    if args.remote:
        query = urlencode({"project": config["project"]})
        result = _request(config, args.api_url, "GET", f"/v1/policy/export?{query}")
        if isinstance(result, dict):
            content = _yaml_mapping(result)
        else:
            content = str(result)
    else:
        policy_path = root / CONFIG_DIR / "policy.json"
        if not policy_path.exists():
            raise CliError("No policy found. Run `agentpgo optimize` first.")
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CliError(f"Could not read {policy_path}: {exc}") from exc
        content = _yaml_mapping(policy)
        result = policy
    output = Path(args.output).expanduser() if args.output else root / "agentpgo-policy.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    summary = {"status": "exported", "output": str(output), "project": config["project"]}
    _print(summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentpgo", description="Profile and optimize an AI agent workload")
    parser.add_argument("--api-url", help="API base URL (or AGENTPGO_API_URL)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize a local AgentPGO project")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--project")
    init.add_argument("--api-url", dest="api_url")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=cmd_init)

    profile = subparsers.add_parser("profile", help="validate and profile a JSON/JSONL trace file")
    profile.add_argument("input")
    profile.add_argument("--remote", action="store_true", help="send the profile to the AgentPGO API")
    profile.set_defaults(handler=cmd_profile)

    eval_parser = subparsers.add_parser("eval", help="manage evaluation datasets")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_import = eval_subparsers.add_parser("import", help="import a JSON/JSONL evaluation dataset")
    eval_import.add_argument("input")
    eval_import.add_argument("--remote", action="store_true", help="send the dataset to the AgentPGO API")
    eval_import.set_defaults(handler=cmd_eval_import)

    optimize = subparsers.add_parser("optimize", help="produce an optimization policy")
    optimize.add_argument("--remote", action="store_true", help="run optimization through the AgentPGO API")
    optimize.set_defaults(handler=cmd_optimize)

    export = subparsers.add_parser("export", help="export the latest policy as YAML")
    export.add_argument("--output", "-o")
    export.add_argument("--remote", action="store_true", help="fetch the policy from the AgentPGO API")
    export.set_defaults(handler=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except CliError as exc:
        print(f"agentpgo: error: {exc}", file=sys.stderr)
        return 2
    return 0


__all__ = ["build_parser", "main"]
