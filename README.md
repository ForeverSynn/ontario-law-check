# ontario-law-check

A small tool that pulls the current text of Ontario and federal Canadian laws straight from the government sites and tells you when something in it isn't in force yet, has been repealed, or is about to change.

I run a one-person IT business in Ontario. I kept needing to check what the Consumer Protection Act or CASL actually says, and AI assistants kept quoting law from memory, sometimes old versions, sometimes text that was never in force. So I wrote this to make the assistant (and me) read the real thing every time.

It comes in three pieces:

- `law.py` is a command line tool. Python 3.8 or newer, standard library only, nothing to install.
- `mcp_server.py` is an MCP server so Claude, OpenCode or any other MCP client can use it as a tool.
- `skill/SKILL.md` is an optional skill file that tells an AI assistant how to use the tool properly (cite things right, flag anything not in force, say when you need a lawyer).

**This is not legal advice.** It's a research aid. See [DISCLAIMER.md](DISCLAIMER.md).

## What it reads

- Ontario: [e-Laws](https://www.ontario.ca/laws), the consolidated statutes and regulations.
- Federal: [Justice Laws](https://laws-lois.justice.gc.ca), the consolidated Acts and regulations.

Nothing is bundled. Every lookup goes to the official site. Downloads are cached for 3 hours so you're not hammering them. The cache lives in `~/.cache/law-lookup-cache` (on Windows, `%TEMP%\law-lookup-cache`), and you can point `LAW_CACHE_DIR` somewhere else if you want.

## Quick start

```
python law.py on 02c30 43 "43(2)"            Ontario statute sections (code = the e-Laws URL slug)
python law.py on "O. Reg. 17/05" 34          Ontario regulation (same as r:050017)
python law.py on r:200522 sch1:2             Ontario schedule item (sch1 = the whole schedule)
python law.py on 02c30 --find "direct agreement"
python law.py on 02c30 --define "consumer"
python law.py on 23c23 --status              status, currency and commencement orders
python law.py ca E-1.6 6 "6(6)"              federal Act
python law.py ca SOR/2012-36 2               federal regulation
python law.py ca P-8.6 sch1:4.3              federal schedule clause (also schII.1, and schV:II:2 = Part II, item 2)
python law.py ca SI/97-5 order               an order with no numbered sections (preamble works the same way)
```

Options: `--history`, `--fresh` (skip the cache), `--json`, `--no-orders`, `--source` (as-enacted source law, off by default).

Exit codes: 0 ok, 2 bad usage, 3 fetch or parse failure, 4 not found, 5 the text you asked for isn't in force (not yet in force, repealed or revoked). With several sections, 4 wins if any are missing, then 5.

No Python? `uv run --script law.py ...` works too. On Windows, `py` works in place of `python` if that's what you have.

To find a code, search `site:ontario.ca/laws <name>` or `site:laws-lois.justice.gc.ca <name>`. The code is the last bit of the URL. `skill/SKILL.md` has a list of common ones.

## Using it as an MCP server

The server speaks MCP over stdio and gives you four tools: `law_lookup`, `law_find`, `law_define` and `law_status`. Keep `mcp_server.py` in the same folder as `law.py`.

Claude Code:
```
claude mcp add ontario-law -- python /path/to/ontario-law-check/mcp_server.py
```

Claude Desktop (Settings > Developer > Edit Config), under `"mcpServers"`:
```json
"ontario-law": {
  "command": "python",
  "args": ["/path/to/ontario-law-check/mcp_server.py"]
}
```

OpenCode: copy the entry from [examples/opencode.example.json](examples/opencode.example.json) into your `opencode.json`.

On Windows use the full path with doubled backslashes in JSON, like `"C:\\Tools\\ontario-law-check\\mcp_server.py"`. If you use uv instead of Python, the command is `uv` and the args start with `"run", "--script"`.

## The skill

`skill/SKILL.md` is written for Claude's skill format but it's just markdown, so you can paste it into any assistant's instructions. It makes the assistant sort every answer into CONFIRMED (the law's own words settle it), INTERPRETATION, or LAWYER REVIEW, and it never lets it quote law it didn't fetch in that session.

If you want it to check your own documents (contracts, invoices, terms), copy [examples/CHECKLIST.example.md](examples/CHECKLIST.example.md) to `CHECKLIST.md` next to `law.py` and fill in your own business facts. Your real checklist stays on your machine. Don't commit it.

## What it checks, and what it can't

- Ontario: the consolidation period, the currency date e-Laws shows, header notes, grey-shaded not-yet-in-force text (shown as `[NOT YET IN FORCE]`), pending-change notes (including ones elsewhere in a section when you only asked for one subsection), repealed or revoked laws, and the commencement orders on the amending Act's Orders in Council tab. It warns when an order was made on or after the currency date or names a day that's already passed. Older orders may not be listed there.
- Federal: the "current to" line, "AMENDMENTS NOT IN FORCE" (sections, schedules and defined terms), related provisions, and in-force footnotes.
- Changes that wait on another provision coming into force get a CHECK line naming that provision.
- It can't see case law, court rulings that strike something down, or how a regulator actually enforces things. It flags federal transitional provisions but can't apply them for you.

## Tests

```
python test_law.py --offline     fixed sample pages, no network (this is what CI runs)
python test_law.py               everything, including live tests against the real sites (about a minute)
python test_mcp.py               starts the MCP server and talks to it, including junk input
```

The live tests hit real laws, so a failure can mean a bug or that the law or the site changed. Read the message before you "fix" anything.

## Helping out

Bug reports, fixes and new law codes are all welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). The quickest win is usually a page layout the parser doesn't handle yet: open an issue with the command you ran and what came out.

## Copyright of the law text

The tool fetches and prints law text, it doesn't ship any. Both governments allow reproduction of their laws without asking, as long as it's accurate and not passed off as the official version:

- Ontario: © King's Printer for Ontario. See the [Ontario copyright page](https://www.ontario.ca/page/copyright-information).
- Federal: the [Reproduction of Federal Law Order (SI/97-5)](https://laws-lois.justice.gc.ca/eng/regulations/SI-97-5/FullText.html).

So every result ends with "This copy is not an official version.", and Ontario results also print the "© King's Printer for Ontario" line with the year. If you quote Ontario text somewhere public, keep that line with it.

## License

MIT. See [LICENSE](LICENSE).
