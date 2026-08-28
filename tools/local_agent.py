"""local_agent — a minimal agentic driver for locally-hosted models
(issue #285 C3/C4; ADR-44 pre-registers its baseline expectation).

One tool (bash), one loop: prompt -> model -> tool calls -> results ->
model, until the model stops calling tools or --max-turns. Emits
claude-shaped stream-json events (assistant + usage.output_tokens per
turn) so campaign.py's tee/ceiling path and the `local` adapter's
ADR-43 parser (`tokens_generated`) work unchanged.

C4 is the point: --seed pins ollama's sampler (options.seed,
temperature 0), giving the outer loop something the API arms never
had — a REPLAYABLE agent session. CON-8: stream-json to stdout, logs
to stderr, exit 0 iff the session completed under its own power.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

OLLAMA = "http://127.0.0.1:11434/api/chat"
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the working directory; returns stdout+stderr.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def chat(model: str, messages: list, seed: int | None) -> dict:
    options: dict = {"temperature": 0}
    if seed is not None:
        options["seed"] = seed
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "tools": [BASH_TOOL],
            "stream": False,
            "options": options,
        }
    ).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def run_bash(command: str, timeout_s: float) -> str:
    try:
        proc = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, timeout=timeout_s
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return out[-8000:] + (f"\n[exit {proc.returncode}]" if proc.returncode else "")
    except subprocess.TimeoutExpired:
        return f"[command timed out after {timeout_s}s]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("--seed", type=int, default=None, help="C4: sampler seed (replayable)")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--tool-timeout-s", type=float, default=120.0)
    args = parser.parse_args()

    messages: list = [{"role": "user", "content": args.prompt}]
    emit({"type": "system", "model": args.model, "seed": args.seed})
    for turn in range(args.max_turns):
        try:
            reply = chat(args.model, messages, args.seed)
        except Exception as exc:  # noqa: BLE001 — the session dies loudly
            print(f"[local_agent] chat failed: {exc}", file=sys.stderr)
            emit({"type": "result", "subtype": "error", "turns": turn})
            return 1
        msg = reply.get("message") or {}
        out_tokens = int(reply.get("eval_count") or 0)
        emit(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": msg.get("content") or ""}],
                    "usage": {"output_tokens": out_tokens},
                },
            }
        )
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            emit({"type": "result", "subtype": "success", "turns": turn + 1})
            return 0
        for call in calls:
            fn = (call.get("function") or {}).get("name")
            raw = (call.get("function") or {}).get("arguments") or {}
            arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            if fn != "bash":
                result = f"[unknown tool {fn!r}]"
            else:
                command = str(arguments.get("command", ""))
                print(f"[local_agent] bash: {command[:120]}", file=sys.stderr)
                result = run_bash(command, args.tool_timeout_s)
            messages.append({"role": "tool", "content": result})
    emit({"type": "result", "subtype": "max_turns", "turns": args.max_turns})
    return 0


if __name__ == "__main__":
    sys.exit(main())
