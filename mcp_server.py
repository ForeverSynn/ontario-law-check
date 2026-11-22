#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""mcp_server.py - MCP (stdio) server for law.py: quote current Ontario and federal Canadian law.

Tools: law_lookup, law_find, law_define, law_status. Each returns the same text law.py prints,
including every CHECK line. Standard library only (Python 3.8+). Keep this file next to law.py.

Run by an MCP client, for example:  python mcp_server.py
Messages are newline-delimited JSON-RPC 2.0 on stdin/stdout. Logs go to stderr.
"""
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import law  # noqa: E402

SERVER_NAME = "ontario-law"
MAX_TEXT = 200_000      # characters per tool result; section text is cut first, warnings are always kept
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]   # newest first
EXIT_MEANING = {0: "ok", 2: "bad request", 3: "fetch or parse failure", 4: "not found (law, section, phrase or definition)",
                5: "the requested text is not in force (not yet in force, repealed or revoked; "
                   "for a phrase or definition search, every match is in such text)"}
INSTRUCTIONS = (
    "Quotes current Ontario law (e-Laws) and federal law (Justice Laws) from the official sources. "
    "Cite the SOURCE, CONSOLIDATION and CURRENCY lines, and report every line under 'CHECK BEFORE RELYING ON THIS'. "
    "Never present text marked [NOT YET IN FORCE] or repealed as current law. Research aid, not legal advice.")

_SRC = {"type": "string", "enum": ["on", "ca"],
        "description": "on = Ontario e-Laws, ca = federal Justice Laws"}
_CODE = {"type": "string", "description": "Ontario: e-Laws code such as 02c30, r:050017 or 'O. Reg. 17/05'. "
                                          "Federal: Justice Laws code such as E-1.6, P-8.6 or SOR/2012-36."}
_FRESH = {"type": "boolean", "default": False, "description": "Skip the 3-hour cache and download again."}
_ORDERS = {"type": "boolean", "default": True,
           "description": "Ontario: list commencement orders for amending Acts with pending text (default true)."}
_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

TOOLS = [
    {"name": "law_lookup",
     "title": "Quote sections of a law",
     "description": "Quote sections of an Ontario or federal Act or regulation exactly as published, with the law's "
                    "status, currency and any not-in-force or pending-change warnings. Sections look like "
                    "43, 43(2), 10.1, sch1 (a whole schedule), sch1:4.3 (item 4.3 of Schedule 1), schII.1 or "
                    "schV:II:2 (Schedule V, Part II, item 2), order (the text of an order with no numbered "
                    "sections) or preamble.",
     "inputSchema": {"type": "object", "properties": {
         "source": _SRC, "code": _CODE,
         "sections": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20,
                      "description": "Sections or subsections to quote, e.g. [\"43\", \"43(2)\"]"},
         "history": {"type": "boolean", "default": False, "description": "Also print amendment-history lines."},
         "fresh": _FRESH, "orders": _ORDERS},
         "required": ["source", "code", "sections"], "additionalProperties": False},
     "annotations": _RO},
    {"name": "law_find",
     "title": "Find sections that contain a phrase",
     "description": "List the sections of a law that contain a phrase (case and punctuation insensitive), with a "
                    "short snippet each. Use it to locate sections, then quote them with law_lookup.",
     "inputSchema": {"type": "object", "properties": {
         "source": _SRC, "code": _CODE,
         "phrase": {"type": "string", "minLength": 2, "description": "Phrase to search for."},
         "fresh": _FRESH, "orders": _ORDERS},
         "required": ["source", "code", "phrase"], "additionalProperties": False},
     "annotations": _RO},
    {"name": "law_define",
     "title": "Quote a defined term",
     "description": "Quote the definition of a term in a law, with the section it is in.",
     "inputSchema": {"type": "object", "properties": {
         "source": _SRC, "code": _CODE,
         "term": {"type": "string", "minLength": 1, "description": "Defined term, e.g. consumer"},
         "fresh": _FRESH, "orders": _ORDERS},
         "required": ["source", "code", "term"], "additionalProperties": False},
     "annotations": _RO},
    {"name": "law_status",
     "title": "Check whether a law is in force and current",
     "description": "Title, in-force status, consolidation period, currency date, last amendment, header notes and "
                    "(Ontario) commencement orders for pending changes. No section text.",
     "inputSchema": {"type": "object", "properties": {
         "source": _SRC, "code": _CODE, "fresh": _FRESH, "orders": _ORDERS},
         "required": ["source", "code"], "additionalProperties": False},
     "annotations": _RO},
]
_TOOL_NAMES = {t["name"] for t in TOOLS}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class BadArgs(Exception):
    pass


def _str(args, key, required=True, min_len=1):
    v = args.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or len(v.strip()) < min_len:
        raise BadArgs(f"'{key}' must be a non-empty string" + (f" of at least {min_len} characters" if min_len > 1 else ""))
    return v.strip()


def _bool(args, key, default):
    v = args.get(key, default)
    if not isinstance(v, bool):
        raise BadArgs(f"'{key}' must be true or false")
    return v


def run_tool(name, args):
    """Return (text, is_error)."""
    if not isinstance(args, dict):
        raise BadArgs("arguments must be an object")
    allowed = set(next(t for t in TOOLS if t["name"] == name)["inputSchema"]["properties"])
    extra = set(args) - allowed
    if extra:
        raise BadArgs("unknown argument(s): " + ", ".join(sorted(extra)))
    src = _str(args, "source").lower()
    if src not in ("on", "ca"):
        raise BadArgs("'source' must be 'on' (Ontario) or 'ca' (federal)")
    code = _str(args, "code")
    fresh = _bool(args, "fresh", False)
    orders = _bool(args, "orders", True)
    targets, find, define, status_only, history = [], None, None, False, False
    if name == "law_lookup":
        secs = args.get("sections")
        if not isinstance(secs, list) or not secs or len(secs) > 20 or not all(isinstance(s, str) and s.strip() for s in secs):
            raise BadArgs("'sections' must be a list of 1 to 20 section strings, e.g. [\"43\", \"43(2)\"]")
        targets = [s.strip() for s in secs]
        history = _bool(args, "history", False)
    elif name == "law_find":
        find = _str(args, "phrase", min_len=2)
    elif name == "law_define":
        define = _str(args, "term")
    else:
        status_only = True
    try:
        res = law.lookup(src, code, targets, find, define, status_only, history, fresh, False, orders)
    except law.LawError as e:
        return f"ERROR ({EXIT_MEANING.get(e.code, 'error')}): {e}", True
    code_ = law.exit_code(res)
    text = cap(law.format_text(res, history))
    return f"RESULT: {EXIT_MEANING.get(code_, code_)}\n{text}", False


def cap(text, limit=MAX_TEXT):
    """Shorten very long results by cutting quoted section text, never the orders list or the warnings."""
    if len(text) <= limit:
        return text
    cut_at = [i for i in (text.find("\nCOMMENCEMENT ORDERS"), text.find("\nCHECK BEFORE RELYING ON THIS:"),
                          text.rfind("\nQuoted from ")) if i >= 0]
    tail_from = min(cut_at) if cut_at else len(text)
    head, tail = text[:tail_from], text[tail_from:]
    room = max(limit - len(tail) - 200, 2000)
    head = head[:room].rsplit("\n", 1)[0]
    note = (f"\n[Output cut: the quoted text was {len(text):,} characters. Ask for fewer sections or one "
            f"subsection at a time to see the rest. Warnings below are complete.]\n")
    return head + note + tail


def _valid_id(v):
    return isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool))


def handle(msg):
    """Return a response dict, or None when no reply is due (notifications and client responses)."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "method" not in msg and ("result" in msg or "error" in msg):
        return None                                    # a response from the client to a server request; ignore
    mid = msg.get("id")
    if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str) or ("id" in msg and not _valid_id(mid)):
        return {"jsonrpc": "2.0", "id": mid if _valid_id(mid) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    method, params = msg["method"], msg.get("params")
    if "id" not in msg:
        return None                                    # notifications/initialized, cancelled, etc.
    params = {} if params is None else params

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if not isinstance(params, dict):
        return err(-32602, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        ver = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return ok({"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": law.VERSION}, "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "resources/list":
        return ok({"resources": []})
    if method == "resources/templates/list":
        return ok({"resourceTemplates": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in _TOOL_NAMES:
            return err(-32602, f"Unknown tool: {name!r}"[:200])
        try:
            text, is_error = run_tool(name, params.get("arguments") or {})
        except BadArgs as e:
            text, is_error = f"ERROR (bad request): {e}", True
        except Exception as e:                         # never let a bug kill the server
            log(traceback.format_exc())
            text, is_error = f"ERROR (internal): {type(e).__name__}: {e}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    return err(-32601, f"Method not found: {method}"[:200])


def safe_handle(msg):
    try:
        return handle(msg)
    except Exception as e:                             # last line of defence: answer, don't die
        log(traceback.format_exc())
        mid = msg.get("id") if isinstance(msg, dict) and _valid_id(msg.get("id")) else None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {type(e).__name__}"}}


def main():
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig", errors="replace")  # -sig: ignore a leading byte-order mark, which Windows PowerShell can send
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    log(f"{SERVER_NAME} MCP server {law.VERSION} ready")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            try:
                msg = json.loads(line)
            except (ValueError, RecursionError):       # bad or absurdly nested JSON
                out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            else:
                if isinstance(msg, list):              # JSON-RPC batch (older protocol versions)
                    if not msg:
                        out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
                    else:
                        out = [r for r in (safe_handle(m) for m in msg) if r is not None] or None
                else:
                    out = safe_handle(msg)
            text = json.dumps(out, ensure_ascii=True) if out is not None else None
        except Exception as e:                         # no single message may end the server
            log(f"internal error: {type(e).__name__}")
            text = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}})
        if text is not None:
            stdout.write(text + "\n")                  # ASCII-only: lone surrogates can't break the stream
            stdout.flush()


if __name__ == "__main__":
    main()
