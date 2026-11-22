#!/usr/bin/env python3
"""Tests for mcp_server.py. Starts the server as a subprocess and talks JSON-RPC over stdio.
The tool calls read the live e-Laws and Justice Laws sites (3-hour cache).

Run:  python test_mcp.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


class Server:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mcp_server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.n = 0

    def send_raw(self, line):
        self.p.stdin.write((line + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def read(self):
        line = self.p.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout; stderr: " + self.p.stderr.read().decode("utf-8", "replace")[-2000:])
        return json.loads(line.decode("utf-8"))

    def call(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))
        r = self.read()
        assert r.get("id") == self.n, r
        return r

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))

    def tool(self, name, args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        assert "result" in r, r
        res = r["result"]
        return res["content"][0]["text"], res["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


def check(name, fn, srv):
    try:
        fn(srv)
        results.append((name, True, ""))
    except AssertionError as e:
        results.append((name, False, str(e)[:500] or "assertion failed"))
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))


def t_initialize(s):
    r = s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "1"}})
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18", res
    assert res["serverInfo"]["name"] == "ontario-law" and "tools" in res["capabilities"], res
    s.notify("notifications/initialized")


def t_unknown_protocol_version(s):
    r = s.call("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert r["result"]["protocolVersion"] == "2025-11-25", r


def t_ping(s):
    assert s.call("ping")["result"] == {}


def t_tools_list(s):
    tools = s.call("tools/list")["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    assert names == ["law_define", "law_find", "law_lookup", "law_status"], names
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"], t


def t_lookup_cpa_43(s):
    text, err = s.tool("law_lookup", {"source": "on", "code": "02c30", "sections": ["43(1)"], "orders": False})
    assert not err, text
    assert text.startswith("RESULT: ok"), text[:200]
    assert "10 days after the consumer has received the written copy" in text, text[:1500]
    assert "CONSOLIDATION:" in text and "CURRENCY:" in text and "CHECK BEFORE RELYING ON THIS" in text


def t_status_cpa2023(s):
    text, err = s.tool("law_status", {"source": "on", "code": "23c23"})
    assert not err, text
    assert "NOT YET IN FORCE" in text and "not in force" in text.splitlines()[0], text[:300]
    assert "COMMENCEMENT ORDERS" in text and "o251025" in text, text[:2000]


def t_find(s):
    text, err = s.tool("law_find", {"source": "on", "code": "02c30", "phrase": "cooling-off period"})
    assert not err and "s. 43" in text, text[:1500]


def t_define_federal(s):
    text, err = s.tool("law_define", {"source": "ca", "code": "P-8.6", "term": "commercial activity"})
    assert not err and "commercial activity" in text and "definition in s." in text, text[:1500]


def t_not_found_is_error(s):
    text, err = s.tool("law_lookup", {"source": "on", "code": "99zz99", "sections": ["1"]})
    assert err and text.startswith("ERROR (not found (law, section, phrase or definition))"), text


def t_bad_args(s):
    text, err = s.tool("law_lookup", {"source": "on", "code": "02c30", "sections": []})
    assert err and "bad request" in text, text
    text, err = s.tool("law_lookup", {"source": "xx", "code": "02c30", "sections": ["1"]})
    assert err and "'source'" in text, text
    text, err = s.tool("law_status", {"source": "on", "code": "02c30", "bogus": 1})
    assert err and "unknown argument" in text, text
    text, err = s.tool("law_find", {"source": "on", "code": "02c30", "phrase": "x"})
    assert err and "phrase" in text, text


def t_unknown_tool(s):
    r = s.call("tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602, r


def t_unknown_method(s):
    r = s.call("does/not/exist")
    assert r["error"]["code"] == -32601, r


def t_parse_error_then_alive(s):
    s.send_raw("{not json")
    r = s.read()
    assert r["error"]["code"] == -32700 and r["id"] is None, r
    assert s.call("ping")["result"] == {}


def t_source_law_refused(s):
    text, err = s.tool("law_lookup", {"source": "on", "code": "s23023", "sections": ["110"]})
    assert err and "source law" in text, text


def t_non_string_tool_name(s):
    r = s.call("tools/call", {"name": ["law_status"], "arguments": {}})
    assert "error" in r and r["error"]["code"] in (-32602, -32603), r
    assert s.call("ping")["result"] == {}, "server must stay up"


def t_lone_surrogate_echo(s):
    s.n += 1
    s.send_raw('{"jsonrpc":"2.0","id":%d,"method":"\\ud800"}' % s.n)
    r = s.read()
    assert r["id"] == s.n and r["error"]["code"] == -32601, r
    text, err = s.tool("law_lookup", {"source": "on", "code": "02c30", "define": "x\ud800y"})
    assert isinstance(text, str), text
    assert s.call("ping")["result"] == {}, "server must stay up"


def t_empty_batch(s):
    s.send_raw("[]")
    r = s.read()
    assert r["error"]["code"] == -32600 and r["id"] is None, r


def t_client_response_ignored(s):
    s.send_raw('{"jsonrpc":"2.0","id":999,"result":{}}')
    r = s.call("ping")                  # the next line must be the ping reply, not an error about id 999
    assert r["result"] == {}, r


def t_bad_id_rejected(s):
    s.send_raw('{"jsonrpc":"2.0","id":{"x":1},"method":"ping"}')
    r = s.read()
    assert r["error"]["code"] == -32600 and r["id"] is None, r


def t_deep_nesting_survives(s):
    s.send_raw("[" * 100000 + "]" * 100000)
    r = s.read()
    assert r["error"]["code"] in (-32700, -32600), r
    assert s.call("ping")["result"] == {}, "server must stay up"


def t_bom_at_start_is_ignored(s):
    b = Server()                                     # a fresh server: the mark only matters as the first bytes
    try:
        b.p.stdin.write(b"\xef\xbb\xbf" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n")
        b.p.stdin.flush()
        r = b.read()
        assert r.get("result") == {} and r.get("id") == 1, r
        b.n = 1
        assert b.call("ping")["result"] == {}, "server must stay up"
    finally:
        b.close()


TESTS = [t_initialize, t_unknown_protocol_version, t_ping, t_tools_list, t_lookup_cpa_43, t_status_cpa2023, t_find,
         t_define_federal, t_not_found_is_error, t_bad_args, t_unknown_tool, t_unknown_method, t_parse_error_then_alive,
         t_source_law_refused, t_non_string_tool_name, t_lone_surrogate_echo, t_empty_batch, t_client_response_ignored,
         t_bad_id_rejected, t_deep_nesting_survives, t_bom_at_start_is_ignored]

if __name__ == "__main__":
    srv = Server()
    for fn in TESTS:
        check(fn.__name__, fn, srv)
    srv.close()
    bad = [r for r in results if not r[1]]
    for name, ok, msg in results:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {msg}"))
    print(f"\n{len(results) - len(bad)} of {len(results)} passed")
    sys.exit(1 if bad else 0)
