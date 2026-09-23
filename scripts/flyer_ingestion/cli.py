"""Commands used by the Codex flyer skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from scripts.flyer_ingestion.agent_auth import (
    FlyerAgentProvisioner,
    KeychainCredentialStore,
    SupabaseFlyerAgentAuthenticator,
    load_agent_auth_settings,
    load_agent_credentials,
)
from scripts.flyer_ingestion.api import FlyerAgentApi
from scripts.flyer_ingestion.config import load_sources
from scripts.flyer_ingestion.pdf import build_pdf_from_images, validate_pdf
from scripts.flyer_ingestion.runner import AgentIngestionRequest, FlyerAgentRunner
from scripts.flyer_ingestion.state import default_state_path, record_result


def main() -> None:
    """Dispatch a local helper command selected by the skill."""
    parser = _parser()
    args = parser.parse_args()
    if args.command == "validate-config":
        _validate_config(args.config)
    elif args.command == "build-pdf":
        _build_pdf(args.images, args.output)
    elif args.command == "validate-pdf":
        _validate_pdf(args.input, args.expected_pages)
    elif args.command == "provision-agent":
        _provision_agent(args.email)
    elif args.command == "ingest":
        _ingest(args)
    elif args.command == "resume":
        _resume(args)
    else:
        _record_state(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GiroSpesa flyer-ingestion helpers")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config")
    validate.add_argument("--config", type=Path, required=True)
    build = commands.add_parser("build-pdf")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("images", nargs="+", type=Path)
    validate_pdf_parser = commands.add_parser("validate-pdf")
    validate_pdf_parser.add_argument("--input", type=Path, required=True)
    validate_pdf_parser.add_argument("--expected-pages", type=int, required=True)
    provision = commands.add_parser("provision-agent")
    provision.add_argument("--email", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--config", type=Path, required=True)
    ingest.add_argument("--source-id", required=True)
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--valid-from", required=True)
    ingest.add_argument("--valid-to", required=True)
    ingest.add_argument("--poll-interval-seconds", type=float, default=5)
    ingest.add_argument("--max-polls", type=int, default=180)
    resume = commands.add_parser("resume")
    resume.add_argument("--source-id", required=True)
    resume.add_argument("--flyer-id", required=True)
    resume.add_argument("--fingerprint", required=True)
    resume.add_argument("--input", type=Path, required=True)
    resume.add_argument("--poll-interval-seconds", type=float, default=5)
    resume.add_argument("--max-polls", type=int, default=180)
    record = commands.add_parser("record-state")
    record.add_argument("--source-id", required=True)
    record.add_argument("--fingerprint", required=True)
    record.add_argument("--status", required=True)
    record.add_argument("--flyer-id")
    record.add_argument("--state", type=Path, default=default_state_path())
    return parser


def _validate_config(path: Path) -> None:
    sources = load_sources(path)
    print(json.dumps({"enabled_source_ids": [source.source_id for source in sources if source.enabled]}))


def _build_pdf(images: list[Path], output: Path) -> None:
    page_count = build_pdf_from_images(tuple(images), output)
    print(json.dumps({"output": str(output), "page_count": page_count}))


def _validate_pdf(path: Path, expected_pages: int) -> None:
    validate_pdf(path, expected_pages)
    print(json.dumps({"input": str(path), "page_count": expected_pages}))


def _provision_agent(email: str) -> None:
    user_id = FlyerAgentProvisioner(KeychainCredentialStore()).provision(email)
    print(json.dumps({"email": email, "user_id": user_id, "role": "admin"}))


def _ingest(args: argparse.Namespace) -> None:
    source = _source_by_id(args.config, args.source_id)
    result = _run_ingestion(source, args)
    print(json.dumps(result.__dict__))


def _resume(args: argparse.Namespace) -> None:
    request = AgentIngestionRequest(args.source_id, args.input, (), "", "")
    result = _run_resume(request, args)
    print(json.dumps(result.__dict__))


def _source_by_id(config_path: Path, source_id: str):
    for source in load_sources(config_path):
        if source.source_id == source_id and source.enabled:
            return source
    raise ValueError(f"Enabled source not found: {source_id}")


def _run_ingestion(source, args: argparse.Namespace):
    request = _request_from_args(source, args)
    with _authenticated_api() as api:
        runner = FlyerAgentRunner(api, poll_interval_seconds=args.poll_interval_seconds, max_polls=args.max_polls)
        return runner.run(request)


def _run_resume(request: AgentIngestionRequest, args: argparse.Namespace):
    with _authenticated_api() as api:
        runner = FlyerAgentRunner(api, poll_interval_seconds=args.poll_interval_seconds, max_polls=args.max_polls)
        return runner.resume(request, args.fingerprint, args.flyer_id)


def _authenticated_api():
    settings = load_agent_auth_settings()
    return _AuthenticatedApi(settings)


class _AuthenticatedApi:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._http = httpx.Client(timeout=60)

    def __enter__(self) -> FlyerAgentApi:
        token = SupabaseFlyerAgentAuthenticator(self._http).access_token(self._settings, load_agent_credentials())
        return FlyerAgentApi(self._http, self._settings.api_url, token)

    def __exit__(self, *_args) -> None:
        self._http.close()


def _request_from_args(source, args: argparse.Namespace) -> AgentIngestionRequest:
    return AgentIngestionRequest(
        source_id=source.source_id,
        pdf_path=args.input,
        supermarket_ids=source.target_supermarket_ids,
        valid_from=args.valid_from,
        valid_to=args.valid_to,
    )


def _record_state(args: argparse.Namespace) -> None:
    record = record_result(
        args.state,
        source_id=args.source_id,
        fingerprint=args.fingerprint,
        status=args.status,
        flyer_id=args.flyer_id,
    )
    print(json.dumps({"status": record.status, "state": str(args.state)}))


if __name__ == "__main__":
    main()
