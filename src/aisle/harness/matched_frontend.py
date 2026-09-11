"""Observed frontend tool events; this parser does not attest complete coverage."""

from __future__ import annotations

import json

_CODEX_TOOLS = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
}
_CODEX_OTHER = {"agent_message", "reasoning", "todo_list", "error"}


class ToolObserver:
    """Consume each event once; retain identities independently of the writable log."""

    def __init__(self, agent):
        self.agent = agent
        self.calls = {}
        self.line = 0
        self.error = (
            None
            if agent in {"codex", "claude", "codex_app_server"}
            else "unsupported frontend event schema"
        )

    def _observe(self, identity, kind, phase):
        if not isinstance(identity, str) or not identity or not isinstance(kind, str) or not kind:
            raise ValueError("frontend tool identity or kind is missing")
        prior = self.calls.get(identity)
        if prior is not None and prior["kind"] != kind:
            raise ValueError("frontend tool identity changed kind")
        if prior is not None and (prior["completed"] or phase == "started"):
            raise ValueError("frontend tool lifecycle repeats or reverses an identity")
        if prior is None:
            prior = {
                "id": identity,
                "kind": kind,
                "completed": False,
                "start_observed": phase == "started",
                "event_lines": [],
            }
            self.calls[identity] = prior
        prior["completed"] = phase == "completed"
        prior["event_lines"].append(self.line)

    def feed(self, line):
        if self.error is not None:
            return
        self.line += 1
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("frontend event must be an object")
            if self.agent == "codex_app_server":
                method = event.get("method")
                if method not in {"item/started", "item/completed"}:
                    return
                item = event.get("params", {}).get("item")
                if type(item) is not dict:
                    raise ValueError("app-server item is missing")
                kind = item.get("type")
                if kind == "subAgentActivity" and item.get("kind") == "completed":
                    # A task finishing is lifecycle telemetry, not a new invocation.
                    return
                if kind in {
                    "userMessage",
                    "hookPrompt",
                    "agentMessage",
                    "functionCallOutput",
                    "plan",
                    "reasoning",
                    "enteredReviewMode",
                    "exitedReviewMode",
                    "contextCompaction",
                }:
                    return
                if kind not in {
                    "commandExecution",
                    "fileChange",
                    "mcpToolCall",
                    "dynamicToolCall",
                    "collabAgentToolCall",
                    "subAgentActivity",
                    "webSearch",
                    "imageView",
                    "sleep",
                    "imageGeneration",
                }:
                    raise ValueError("unknown app-server item type")
                self._observe(item.get("id"), kind, method.split("/")[1])
                return
            event_type = event.get("type")
            if not isinstance(event_type, str) or not event_type.strip():
                raise ValueError("frontend event type is missing or invalid")
            if self.agent == "codex":
                if event_type not in {"item.started", "item.updated", "item.completed"}:
                    if event_type.startswith("item."):
                        raise ValueError("unknown frontend item lifecycle")
                    return
                item = event.get("item")
                if not isinstance(item, dict):
                    raise ValueError("frontend item is missing")
                kind = item.get("type")
                if kind in _CODEX_OTHER:
                    return
                if kind not in _CODEX_TOOLS:
                    raise ValueError("unknown frontend item type")
                self._observe(item.get("id"), kind, event["type"].split(".")[1])
            else:
                if event.get("type") not in {"assistant", "user"}:
                    return
                message = event.get("message")
                if not isinstance(message, dict):
                    raise ValueError("frontend message is missing")
                content = message.get("content")
                if isinstance(content, str):
                    return
                if not isinstance(content, list):
                    raise ValueError("frontend message content is invalid")
                for block in content:
                    if not isinstance(block, dict):
                        raise ValueError("frontend content block is invalid")
                    block_type = block.get("type")
                    if not isinstance(block_type, str) or not block_type.strip():
                        raise ValueError("frontend content block type is missing or invalid")
                    if block_type == "tool_use":
                        self._observe(block.get("id"), block.get("name"), "started")
                    elif block_type == "tool_result":
                        identity = block.get("tool_use_id")
                        if not isinstance(identity, str) or identity not in self.calls:
                            raise ValueError("frontend tool result has no observed invocation")
                        self._observe(identity, self.calls[identity]["kind"], "completed")
        except (ValueError, TypeError) as exc:
            self.error = str(exc)

    def report(self):
        import copy

        return {
            "schema_version": "aisle.matched-frontend-tools.v1",
            "agent": self.agent,
            "ok": self.error is None,
            "complete_coverage": False,
            "observed_calls": len(self.calls) if self.error is None else None,
            "completed_calls": (
                sum(call["completed"] for call in self.calls.values())
                if self.error is None
                else None
            ),
            "pending_ids": [key for key, call in self.calls.items() if not call["completed"]],
            "calls": copy.deepcopy(list(self.calls.values())),
            "error": self.error,
        }


class FrontendToolBudget:
    """Live guard for an explicitly declared observed-frontend-call ceiling."""

    def __init__(self, agent, ceiling, *, admission_controlled=False):
        if type(ceiling) is not int or ceiling <= 0:
            raise ValueError("frontend tool ceiling must be a positive integer")
        if (
            type(admission_controlled) is not bool
            or admission_controlled
            and agent != "codex_app_server"
        ):
            raise ValueError("dispatch enforcement requires the owned App Server")
        self.admission_controlled = admission_controlled
        self.observer = ToolObserver(agent)
        self.ceiling = ceiling

    def __call__(self, line):
        self.observer.feed(line)
        if self.observer.error is not None:
            raise ValueError(f"frontend telemetry invalid: {self.observer.error}")
        return (
            "frontend_tool_budget"
            if not self.admission_controlled and len(self.observer.calls) > self.ceiling
            else None
        )

    def report(self):
        return {
            **self.observer.report(),
            "ceiling": self.ceiling,
            "source": "live_stdout_pipe",
            **({"enforcement": "dispatch"} if self.admission_controlled else {}),
        }


def observe_tools(agent: str, lines) -> dict:
    """Observe identified frontend calls without asserting complete coverage or broker linkage."""
    observer = ToolObserver(agent)
    for line in lines:
        observer.feed(line)
    return observer.report()


def verify_live_report(agent, ceiling, lines, live, stopped, *, admission_controlled=False):
    """Replay the same guarded prefix, allowing buffered output after a stop."""
    guard = FrontendToolBudget(agent, ceiling, admission_controlled=admission_controlled)
    reason = None
    for line in lines:
        try:
            reason = guard(line)
        except ValueError:
            break
        if reason is not None:
            break
    if json.dumps(live, sort_keys=True, allow_nan=False) != json.dumps(
        guard.report(), sort_keys=True, allow_nan=False
    ):
        raise ValueError("live frontend report differs from retained guarded transcript")
    if (reason == "frontend_tool_budget") != (stopped == "frontend_tool_budget"):
        raise ValueError("live frontend budget stop disagrees with process record")
