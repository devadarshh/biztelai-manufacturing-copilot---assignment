from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

QUESTIONS = [
    "What is the recommended coolant concentration for the TX-200 CNC turning centre?",
    "The machine is showing alarm AL-104. What does it mean and what should the operator do?",
    "Who is allowed to remove a lockout lock from a machine?",
    "What is the outer diameter tolerance for the BH-220 bearing housing?",
    "How often should spindle runout be checked, and who does it?",
    "What happens if the supplier's Material Test Certificate is missing?",
    "How many samples should be inspected for a lot of 1,000 pieces?",
    "What is the normal hydraulic system pressure?",
    "What is the procedure for removing a lock when its owner is absent?",
    "What is the factory manager's salary?",
]


def ask(api_base: str, question: str) -> dict:
    request = Request(
        f"{api_base.rstrip('/')}/ask",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.load(response)


def render_report(results: list[dict]) -> str:
    lines = [
        "# Assignment question results",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "These are responses returned by the running application; expected answers are not inserted by this script.",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"## {index}. {result['question']}",
                "",
                f"**Result:** {result['status']}",
                "",
                result.get("answer", result.get("error", "No response was returned.")),
                "",
            ]
        )
        sources = result.get("sources", [])
        if sources:
            lines.append("**Cited sources:**")
            for source in sources:
                details = [source.get("document", "unknown document")]
                if source.get("page") is not None:
                    details.append(f"page {source['page']}")
                if source.get("revision"):
                    details.append(f"revision {source['revision']}")
                if source.get("revision_status"):
                    details.append(source["revision_status"])
                lines.append(f"- {', '.join(details)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ten assignment questions against a running local API.")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("ASSIGNMENT_API_BASE_URL", "http://127.0.0.1:8000/api"),
        help="API base URL, ending in /api (default: http://127.0.0.1:8000/api)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "assignment_qa_results.md",
        help="Markdown report path",
    )
    args = parser.parse_args()

    results = []
    for question in QUESTIONS:
        try:
            response = ask(args.api_base, question)
            results.append(
                {
                    "question": question,
                    "status": "answered" if response.get("found_in_documents") else "unsupported",
                    "answer": response.get("answer", ""),
                    "sources": response.get("sources", []),
                }
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            results.append(
                {
                    "question": question,
                    "status": "request failed",
                    "error": f"{type(exc).__name__}: request failed; check the API and backend logs.",
                    "sources": [],
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(results), encoding="utf-8")
    print(f"Wrote {len(results)} actual API results to {args.output}")


if __name__ == "__main__":
    main()
