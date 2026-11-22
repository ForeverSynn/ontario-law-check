---
name: ontario-law-check
description: Quote current Ontario and federal Canadian law from official sources (e-Laws, Justice Laws) and check documents against it. Use for Ontario or Canadian legal and compliance questions.
---

# Ontario law check

Research aid built on official sources. Not legal advice.

## Scope
- Ontario law first, then federal where it applies (privacy, anti-spam, tax, competition, criminal, copyright). Municipal by-laws are separate: read the municipality's own by-law.
- If a `CHECKLIST.md` sits beside `law.py`, it holds the user's own business facts and topics. Read it for document checks (see examples/CHECKLIST.example.md).

## Tier every finding
- **CONFIRMED**: the law's own words, fetched this session, settle it. Quote and cite. No lawyer warning.
- **INTERPRETATION**: clear text applied to the user's facts, or official guidance (CRTC, CRA, OPC, Competition Bureau). Say what the reading rests on. No lawyer warning.
- **LAWYER REVIEW**: only when the tool and the law text can't settle it:
  1. key words are undefined or ambiguous for these facts, or case law decides it;
  2. the text that matters is not in force, pending, contingent or transitional, and timing matters;
  3. sources conflict, or guidance is silent;
  4. a dispute, demand letter, tribunal, court or police matter, or real money or liability is at stake;
  5. it is on the lawyer questions list (CHECKLIST.md).
  Write: `LAWYER REVIEW: <why the law text can't settle it>. Ask: <one question>.`
- Tier each part of a mixed point (the duty CONFIRMED, its application LAWYER REVIEW). Never delay a plain duty, such as reporting a child at risk, to wait for a lawyer.
- Licensing or registration a regulator decides (ESA, WSIB, RPRA, the municipality): ask the regulator first, in writing, and keep the reply.
- End each answer with "Research from official sources, not legal advice." No lawyer lines on CONFIRMED items.

## Rules
1. Quote law only from text the tool fetched this session. Never from memory. If a fetch fails, say so and give the URL.
2. Cite name, citation, section, URL and currency exactly as the tool prints them (schedule items print as "Sch. 1, 4.1.3"). Ontario: the "Consolidation Period" line plus the e-Laws currency date (if the period's "from" date is later, the law is current to that date). Federal: the Justice Laws "current to ... last amended on ..." line, never the XML date. Add "retrieved YYYY-MM-DD". When Ontario text is quoted in anything published, keep the tool's "© King's Printer for Ontario" line with it.
3. Report every line under "CHECK BEFORE RELYING ON THIS". Never treat text marked [NOT YET IN FORCE], repealed, or found only in "AMENDMENTS NOT IN FORCE" as current law. Notes marked [ELSEWHERE IN THIS SECTION] can still change the subsection asked about: report them.
4. Pending Ontario text: the tool lists the commencement orders on the amending Act's e-Laws Orders in Council tab (older orders may not be listed; check The Ontario Gazette for those). If an order was made on or after the currency date, or names a day that has passed, read it and say what it changes.
5. The tool can't see case law, enforcement practice, or court rulings that strike a provision (e-Laws is not known to show them), and can't apply transitional rules (it flags federal "related provisions"; read them). A change waiting on another provision ("On the day section X ... comes into force") gets a CHECK line: look that provision up. If any of this could change the answer, use LAWYER REVIEW.
6. Stay lean: `--find` to locate, then only the sections needed; `--status` for in-force questions. Don't paste whole Acts.
7. Guidance is not law: cite the page and its "Date modified" (or say it is blank). crtc.gc.ca shows a bot check (never get past it): use WebFetch and quote briefly. WebFetch also for canada.ca and competition-bureau.canada.ca. priv.gc.ca fetches directly. Municipal PDFs can refuse a direct fetch (403): use WebFetch, ask for named sections word for word. WebFetch's text can stop before a long PDF ends: if the section isn't reached, say so and ask the municipality. Never use WebFetch for e-Laws law text (it served a stale consolidation).
8. If CHECKLIST.md is present, apply its facts (client types, where deals are made, address rules) and its lawyer questions.

## Tool
`law.py` v2.4, Python 3.8+ standard library. The `ontario-law` MCP server (tools `law_lookup`, `law_find`, `law_define`, `law_status`) runs the same code. Use whichever is available.
```
python law.py on 02c30 43 "43(2)"          Ontario statute (code = e-Laws URL slug)
python law.py on "O. Reg. 17/05" 34        Ontario regulation (same as r:050017)
python law.py on r:200522 sch1:2           Ontario schedule item (sch1 = whole schedule)
python law.py on 02c30 --find "direct agreement"
python law.py on 02c30 --define "consumer"
python law.py on 23c23 --status            status, currency and commencement orders
python law.py ca E-1.6 6 "6(6)"            federal Act
python law.py ca SOR/2012-36 2             federal regulation
python law.py ca P-8.6 sch1:4.3            federal schedule clause; schII.1, schV:II:2 (Part II, item 2) also work
python law.py ca SI/97-5 order             an order with no numbered sections (preamble works the same way)
```
Options: `--history`, `--fresh` (skip the 3-hour cache), `--json`, `--no-orders`, `--source` (as-enacted source law, refused by default). Exit codes: 0 ok, 2 bad usage, 3 fetch or parse failure, 4 not found (law, section, phrase or definition), 5 requested text not in force, repealed or revoked (for `--find`/`--define`: every match is).
Unknown code: search `site:ontario.ca/laws <name>` or `site:laws-lois.justice.gc.ca <name>`. If the tool can't run: fetch e-Laws with user agent `curl/8.5.0` (browsers get a script shell) and `https://laws-lois.justice.gc.ca/eng/XML/<code>.xml` (a regulation's "/" becomes "-", e.g. `SOR-2012-36.xml`), and check currency and notes by hand. Case law: CanLII (canlii.org) in a browser; its API is metadata only and GitHub canlii-mcp servers are unofficial.

## Codes (checked 2026-11-21)
Ontario (`on`): CPA 2002 `02c30` · O. Reg. 17/05 `r:050017` · CPA 2023, NOT IN FORCE `23c23` (`s23023` is the as-enacted Bill 142) · Business Names Act `90b17`, O. Reg. 399/21 `r:210399` · ESA 2000 `00e41` · AODA `05a11`, IASR `r:110191` · Limitations Act `02l24` · Repair and Storage Liens Act `90r25` · Electronic Commerce Act `00e17` · Legislation Act `06l21` · PHIPA `04p03`, O. Reg. 329/04 `r:040329` · Sale of Goods Act `90s01` · Human Rights Code `90h19` · WSIA `97w16`, O. Reg. 175/98 `r:980175` · Electricity Act `98e15`, O. Reg. 570/05 `r:050570`, O. Reg. 438/07 `r:070438` · Collection and Debt Settlement Services Act `90c14` · CYFSA `17c14` · O. Reg. 522/20 (e-waste) `r:200522`, O. Reg. 30/20 (batteries) `r:200030`
Federal (`ca`): CASL `E-1.6`, `SOR/2012-36`, `SOR/2013-221` · PIPEDA `P-8.6`, `SOR/2018-64` · Excise Tax Act `E-15` · Income Tax Act `I-3.3` (large) · Competition Act `C-34` · Criminal Code `C-46` · Copyright Act `C-42` · Interest Act `I-15` · Canada Consumer Product Safety Act `C-1.68` · `I-20.7` (mandatory reporting of Internet child sexual abuse and exploitation material; long title only, the short title "Mandatory Reporting Act" is pending, 2026, c. 19, s. 178) · Canada Pension Plan `C-8`

## Topic index (small Ontario service business)
Where to start for common questions. Pull each section with the tool before relying on it. If the user has a `CHECKLIST.md`, read that topic's line there first.
- Client type: CPA ss. 1, 2(1) · Every consumer agreement: CPA ss. 5, 7-11, 13-17, 93
- Direct (in-home) agreements: CPA ss. 20, 41-43.1, 92-99; O. Reg. 17/05 ss. 34-35.1, 79, 83, 84; Legislation Act ss. 88-89
- Online, email and phone deals: CPA ss. 20, 22-23, 37-40, 44-47; O. Reg. 17/05 ss. 17-18, 24, 31-33, 35-39
- Monthly support plan: CPA ss. 13, 21-26; O. Reg. 17/05 ss. 17-19, 23.1-25, 41-42
- Clauses on time to sue: Limitations Act ss. 4, 22 · Holding devices for payment: CPA ss. 16, 55; RSLA ss. 1, 3, 7, 10, 14, 15; O. Reg. 17/05 Part VI
- Business name: BNA ss. 2, 4, 7, 10; SOR/2012-36 s. 2 · GST/HST and CPP: ETA ss. 148, 166, 221, 240, 286; ITA s. 230; CPP s. 10
- Commercial emails (CASL): CASL ss. 6, 10, 11; SOR/2012-36 ss. 2-3; SOR/2013-221 s. 3 · Installing software and remote tools: CASL ss. 8, 10, 11(5); SOR/2012-36 ss. 4-5; SOR/2013-221 s. 6
- Authority before access: Criminal Code ss. 162, 184, 342.1, 342.2, 430(1.1) · Client data (PIPEDA): PIPEDA ss. 4, 10.1-10.3, Sch. 1; SOR/2018-64 s. 6
- Health clinic clients (PHIPA): PHIPA ss. 2, 10(4), 17, 40, 43; O. Reg. 329/04 ss. 6-7 · Smart home and wiring: O. Reg. 570/05 ss. 2-3; O. Reg. 438/07 s. 5
- Advertising: Competition Act ss. 52(1.3), 74.01, 74.02 · Parts and goods sold: Sale of Goods Act ss. 13-16, 53; CPA s. 9; CCPSA ss. 13-14
- Software licences: Copyright Act ss. 27, 38.1, 41, 41.1, 41.12-41.18, 41.121 · E-documents: Electronic Commerce Act ss. 3, 11, 21, 31
- Equal service: Human Rights Code ss. 1, 17 · Late fees and interest: Interest Act ss. 3-4; Criminal Code s. 347; CPA ss. 66-67, 75; CDSSA s. 4
- WSIB: WSIA ss. 12.2, 12.3, 75 · Municipal licensing: the municipality's licensing by-law
- Found illegal content: Criminal Code s. 163.1; CYFSA s. 125 (reasonable grounds to suspect sexual abuse or exploitation of a child under 16 of a kind in s. 125(1) paras. 3-4.2: report the suspicion yourself, immediately, to a children's aid society, and again on new grounds: s. 125(1)-(4)); `I-20.7` ss. 1-5
- E-waste and batteries: O. Reg. 522/20 ss. 1, 3, 22, 27, 30; O. Reg. 30/20 ss. 1, 21, 26, 29 · Accessibility (AODA): O. Reg. 191/11 ss. 1-2 · Hiring: ESA 2000 (`00e41`) · Watch: CPA 2023 (`23c23`) not in force

## Checking a document
1. Document type, client type (consumer, business or mixed), and where and how it is agreed (client's home, the supplier's place of business, online, phone or email).
2. Map the topic index (and CHECKLIST.md lines if present); pull each section with the tool.
3. Table: requirement | law quoted and cited | what the document says | meets / gap / unclear | tier | fix.
4. List LAWYER REVIEW items with their questions. 
