#!/usr/bin/env python3
"""Scan code files for common secret-leak risks without printing secrets."""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EXCLUDES = {
    ".venv",
    "__pycache__",
    "output_video",
    "output_date",
    ".git",
}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".env",
    ".example",
}
PATTERNS = [
    ("api_key_literal", re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret)\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
    ("dashscope_or_openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("cookie_header", re.compile(r"(?i)\bcookie\s*:\s*[A-Za-z0-9_%-]+=")),
    ("douyin_session_cookie", re.compile(r"\b(sessionid|sessionid_ss|sid_guard|uid_tt|uid_tt_ss|passport_auth_status|ttwid)=[^;\s]{8,}")),
]


def should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in DEFAULT_EXCLUDES for part in rel_parts):
        return True
    if path.name.startswith("cookie") and path.suffix in {".txt", ""}:
        return True
    return False


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path, root):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", "requirements.txt"}:
            yield path


def scan(root: Path) -> list[dict]:
    findings: list[dict] = []
    for path in iter_text_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, 1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "file": str(path.relative_to(root)),
                            "line": line_no,
                            "type": label,
                        }
                    )
    return findings


def write_report(root: Path, output: Path, findings: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Security Audit Report",
        "",
        f"- Root: {root}",
        f"- Scanned at: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        f"- Findings: {len(findings)}",
        "",
        "This report intentionally lists only file, line, and finding type. It does not print secret values.",
        "",
    ]
    if findings:
        lines.extend(["| # | File | Line | Type |", "|---:|---|---:|---|"])
        for index, finding in enumerate(findings, 1):
            lines.append(f"| {index} | {finding['file']} | {finding['line']} | {finding['type']} |")
    else:
        lines.append("No hardcoded API keys or cookie values were found in the scanned code files.")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan this package for common secret leaks.")
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    parser.add_argument("--output", type=Path, default=BASE_DIR / "output_date" / "security_audit_report.md")
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()

    findings = scan(args.root.resolve())
    write_report(args.root.resolve(), args.output, findings)
    print(f"findings={len(findings)}")
    print(f"report={args.output.resolve()}")
    if findings and args.fail_on_findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
