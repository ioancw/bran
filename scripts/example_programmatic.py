"""Example: drive bran agents from a regular Python script.

Run with:  python scripts/example_programmatic.py
"""

from __future__ import annotations

from bran import run_agent_sync


def main() -> None:
    record = run_agent_sync(
        "research",
        "In two sentences, what is the Claude Agent SDK and what is it good for?",
    )
    print(f"--- result (run {record.id[:8]}, {record.status}) ---")
    print(record.result or "(no result)")
    print(f"\ncost: ${record.total_cost_usd or 0:.4f} · "
          f"turns: {record.num_turns} · session: {record.session_id}")


if __name__ == "__main__":
    main()
