# Contributing

Thanks for wanting to help. Here's how it works.

## Ground rules

- Standard library only. People should be able to drop `law.py` on any machine with Python 3.8+ and run it. No pip installs.
- Never bundle law text. The tool always fetches from e-Laws or Justice Laws. Small made-up snippets in the offline tests are fine (and preferred over real text).
- Don't make it guess. If the tool can't tell whether something is in force, it should say so with a CHECK line, not pick an answer.
- Nothing personal in commits. No real client info, addresses, or your own CHECKLIST.md.

## Reporting a bug

Open an issue with:
1. the exact command (or MCP call) you ran,
2. what it printed,
3. what the official page actually says, with the URL.

## Making a change

1. Fork, make a branch.
2. Add or update an offline test in `test_law.py` that shows the bug or the new behaviour (look at the `t_off_*` tests for the pattern).
3. Run `python test_law.py --offline` and `python test_mcp.py`. If you touched parsing, run the full `python test_law.py` too.
4. Open a pull request saying what changed and why. Keep it to one thing per PR if you can.

The offline tests run on every push and pull request (Python 3.8 and the newest Python, Linux and Windows). The live tests run once a month against the real sites, so a red live run usually means a law or a site changed.

## Adding law codes to the skill

The codes list in `skill/SKILL.md` is checked by hand. If you add one, run `python law.py <on|ca> <code> --status` first and put the date you checked it in the PR.

## Scope

Ontario and federal law. Other provinces would be great but they'd need their own parsers, so open an issue first so we can talk about how to fit it in.
