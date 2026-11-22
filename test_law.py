#!/usr/bin/env python3
"""Live regression tests for law.py. They read the real e-Laws and Justice Laws sites.

Run:  python test_law.py            (uses the 3-hour cache)
      python test_law.py --fresh    (forces new downloads)
      python test_law.py --offline  (only the tests that need no network; CI runs these)
A failure means either a bug in law.py or that the law or the site changed. Read the message before fixing.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import law  # noqa: E402

FRESH = "--fresh" in sys.argv
results = []


def run(src, code, targets=(), **kw):
    return law.lookup(src, code, list(targets), fresh=FRESH, **kw)


def text_of(res, i=0):
    return "\n".join(r["text"] for r in res["sections"][i]["rows"])


def levels(res):
    return [f["level"] for f in res["flags"]]


def check(name, fn):
    try:
        fn()
        results.append((name, True, ""))
    except AssertionError as e:
        results.append((name, False, str(e) or "assertion failed"))
    except Exception as e:  # network or parse errors count as failures
        results.append((name, False, f"{type(e).__name__}: {e}"))


def t_cpa_header():
    r = run("on", "02c30", status_only=True)
    assert r["title"].startswith("Consumer Protection Act, 2002"), r["title"]
    assert r["status"].startswith("IN FORCE (but repealed"), r["status"]
    assert "From" in (r["consolidation"] or ""), r["consolidation"]
    assert r["currency"] and "e-Laws currency date" in r["currency"], r["currency"]


def t_cpa_43():
    r = run("on", "02c30", ["43"])
    t = text_of(r)
    assert "until 10 days after the consumer has received the written copy of the agreement" in t, t[:300]
    assert r["sections"][0]["headnote"] == "Cancellation: cooling-off period", r["sections"][0]["headnote"]


def t_cpa_43_2():
    r = run("on", "02c30", ["43(2)"])
    t = text_of(r)
    assert t.startswith("(2) In addition to the right under subsection (1)"), t[:200]
    assert "(1.1)" not in t, "subsection filter leaked other subsections"


def t_cpa_10():
    t = text_of(run("on", "02c30", ["10"]))
    assert "exceeds the estimate by more than 10 per cent" in t, t[:300]


def t_cpa_47_1_pending():
    r = run("on", "02c30", ["47.1"])
    rows = r["sections"][0]["rows"]
    assert any(x["nif"] for x in rows), "no not-in-force rows marked"
    assert any(not x["nif"] for x in rows), "current text missing"
    assert "PENDING" in levels(r) and "NOT_IN_FORCE" in levels(r), levels(r)


def t_cpa_find():
    r = run("on", "02c30", find="cooling-off period")
    secs = [m["section"] for m in r["matches"]]
    assert secs == ["28", "35", "43", "51"], secs


def t_cpa_define():
    r = run("on", "02c30", define="direct agreement")
    d = r["definitions"]
    assert d and d[0]["section"] == "20", d
    assert "negotiated or concluded in person at a place other than" in d[0]["rows"][0]["text"]
    r = run("on", "02c30", define="consumer")
    assert r["definitions"] and r["definitions"][0]["section"] == "1"


def t_cpa_repealed_range():
    r = run("on", "02c30", ["65.15"])
    assert "REPEALED" in levels(r), levels(r)
    assert law.exit_code(r) == 5


def t_reg_thresholds():
    r = run("on", "r:050017", ["31", "34", "36"])
    for i, s in enumerate(("37", "41 (1)", "44")):
        t = text_of(r, i)
        assert "is $50" in t and s in t, t


def t_reg_35_statement():
    r = run("on", "O. Reg. 17/05", ["35"])
    t = text_of(r)
    assert "Your Rights under the Consumer Protection Act, 2002" in t, "statement heading missing"
    assert "ten (10) days after the day you receive a written copy of the agreement" in t
    assert r["url"].endswith("/regulation/050017"), r["url"]


def t_reg_25_found():
    r = run("on", "r:050017", ["25"])
    assert r["sections"][0]["found"], "s. 25 not found"
    assert "express opportunity to accept or decline" in text_of(r)


def t_cpa2023_not_in_force():
    r = run("on", "23c23", ["1"])
    assert r["status"] == "NOT YET IN FORCE", r["status"]
    assert r["sections"][0].get("only_pending"), "s. 1 not marked as not in force"
    assert law.exit_code(r) == 5


def t_source_law_refused():
    try:
        run("on", "s23023", ["110"])
    except law.LawError as e:
        assert e.code == 2
        return
    raise AssertionError("source law was not refused")


def t_revoked_reg():
    r = run("on", "r:910121", status_only=True)
    assert r["status"].startswith("REVOKED on October 19, 2021"), r["status"]
    assert law.exit_code(r) == 5


def t_limitations_22_pending():
    r = run("on", "02l24", ["22"])
    assert "PENDING" in levels(r), levels(r)
    assert "applies despite any agreement to vary or exclude it" in text_of(r)


def t_rsla_4_pending():
    r = run("on", "90r25", ["4"])
    assert any(x["nif"] for x in r["sections"][0]["rows"]), "RSLA s. 4 not-in-force text not marked"
    assert "PENDING" in levels(r)


def t_iasr_2_first_definition():
    t = text_of(run("on", "r:110191", ["2"]))
    assert "“accessible formats”" in t, t[:200]


def t_legislation_act():
    r = run("on", "06l21", ["10.1", "89"])
    assert any(f["level"] == "PENDING" for f in flags_for(r, "s. 10.1")), r["flags"]
    assert "excludes that day" in text_of(r, 1), text_of(r, 1)[:400]


def t_pipeda_10_1():
    r = run("ca", "P-8.6", ["10.1"])
    t = text_of(r)
    assert "real risk of significant harm to an individual." in t, t[:300]
    assert "(a) the sensitivity of the personal information involved in the breach;" in t, "punctuation lost"
    assert re.search(r"current to \d{4}-\d{2}-\d{2}", r["currency"] or ""), r["currency"]


def t_pipeda_10_4_not_in_force():
    r = run("ca", "P-8.6", ["10.4"])
    e = r["sections"][0]
    assert not e["found"] and e.get("only_pending"), e
    assert law.exit_code(r) == 5


def t_pipeda_10_3_pending():
    assert "PENDING" in levels(run("ca", "P-8.6", ["10.3"]))


def t_pipeda_schedule():
    t = text_of(run("ca", "P-8.6", ["sch1:4.3.8"]))
    assert t.startswith("4.3.8 An individual may withdraw consent at any time"), t[:200]


def t_pipeda_define():
    r = run("ca", "P-8.6", define="commercial activity")
    assert r["definitions"], "no definition found"
    t = r["definitions"][0]["rows"][0]["text"]
    assert t.startswith("commercial activity means any particular transaction, act or conduct"), t[:200]


def t_casl_8():
    t = text_of(run("ca", "E-1.6", ["8"]))
    assert "express consent of the owner or an authorized user" in t, t[:300]


def t_casl_6_6():
    t = text_of(run("ca", "E-1.6", ["6(6)"]))
    assert "provides a quote or estimate" in t and "(7)" not in t, t[:300]


def t_crtc_reg_slash_code():
    r = run("ca", "SOR/2012-36", ["2"])
    assert r["title"].startswith("SOR/2012-36"), r["title"]
    assert "the mailing address" in text_of(r)


def t_gic_reg_3():
    t = text_of(run("ca", "SOR-2013-221", ["3"]))
    assert "in response to a request, inquiry or complaint" in t, t[:300]


def t_interest_act_pending():
    r = run("ca", "I-15", ["4"])
    assert "five per cent per annum" in text_of(r)
    assert levels(r).count("PENDING") >= 2, levels(r)


def t_eta_148():
    t = text_of(run("ca", "E-15", ["148"]))
    assert "$30,000" in t and "four calendar quarters" in t, t[:300]


def t_bad_code():
    try:
        run("on", "99zz99", ["1"])
    except law.LawError as e:
        assert e.code == 4, e.code
        return
    raise AssertionError("bad code did not raise")


def t_cli_exit_codes():
    py = sys.executable
    p = subprocess.run([py, "-I", os.path.join(HERE, "law.py"), "ca", "P-8.6", "10.4"], capture_output=True, text=True)
    assert p.returncode == 5, p.returncode
    p = subprocess.run([py, "-I", os.path.join(HERE, "law.py"), "on", "02c30", "43"], capture_output=True, text=True)
    assert p.returncode == 0 and "CHECK BEFORE RELYING ON THIS" in p.stdout, p.returncode


def t_currency_from_elaws():
    r = run("on", "02c30", status_only=True, orders=False)
    assert "as displayed on e-Laws" in (r["currency"] or ""), r["currency"]
    d = law.parse_date(r["currency_date"] or "")
    assert d and 0 <= (law.datetime.date.today() - d).days <= 21, r["currency_date"]


def t_orders_cpa2023():
    r = run("on", "23c23", status_only=True)
    assert r["enacted_by"] == "s23023", r["enacted_by"]
    info = r["commencement_orders"][0]
    assert info["act_code"] == "s23023" and "Better for Consumers" in info["act"], info
    o = next(o for o in info["orders"] if o["code"] == "o251025")
    assert o["ordered"] == "July 31, 2025", o["ordered"]
    assert any("Schedule 2, s. 7" in t for t in o["text"]), o["text"]
    assert "July 1, 2026" in o["named_days"], o["named_days"]


def t_orders_cpa2002_repeal_note():
    r = run("on", "02c30", status_only=True)
    assert [i["act_code"] for i in r["commencement_orders"]] == ["s23023"], r.get("commencement_orders")


def t_orders_after_currency_flag():
    # O.C. 1616/2026 (ordered Nov 19, 2026) names days for Funeral, Burial and Cremation Services Act amendments.
    r = run("on", "02f33", ["1"])
    info = next(i for i in r["commencement_orders"] if i["act_code"] == "s25024")
    o = next(o for o in info["orders"] if o["code"] == "o261616")
    assert o["mentions_law"], "order should name the Funeral, Burial and Cremation Services Act, 2002"
    other = next(o for o in info["orders"] if o["code"] == "o260372")
    assert not other["mentions_law"], "Christopher's Law order should not match"
    late = law.parse_date(o["ordered"]) > law.parse_date(r["currency_date"])
    flagged = any(f["level"] == "CHECK" and "o261616" in f["text"] and "after the e-Laws currency date" in f["text"]
                  for f in r["flags"])
    assert flagged == late, (late, flagged, r["currency_date"])


def t_orders_off():
    r = run("on", "23c23", status_only=True, orders=False)
    assert "commencement_orders" not in r


def t_parse_oic_offline():
    d = {"title": "ORDER IN COUNCIL 1/2026", "content":
         '<p class="madeappfiled-e">Ordered: March 2, 2026</p><p class="preamble-e">... orders that:</p>'
         '<p class="Pheading-e">Some Act, 2025</p><p class="paragraph-e">1. May 1, 2026 is named as the day on which'
         ' the following provisions come into force:</p><p class="parawindt3-e">Schedule 1, s. 2.</p>'
         '<p class="number-e">Minister of Something</p><p class="headingx-e">Correction</p><p class="section">x</p>'}
    o = law.parse_oic(d, "o260001")
    assert o["ordered"] == "March 2, 2026", o
    assert o["text"] == ["1. May 1, 2026 is named as the day on which the following provisions come into force: Schedule 1, s. 2."], o["text"]
    assert o["named_days"] == ["May 1, 2026"], o


def t_missing_subsection_falls_back():
    r = run("ca", "C-34", ["74.01(1.2)"])
    assert any(f["level"] == "CHECK" and "(1.2) was not found" in f["text"] for f in r["flags"]), r["flags"]
    assert "(1.1) For greater certainty" in text_of(r), "whole section should be shown"


def t_competition_drip_pricing():
    r = run("ca", "C-34", ["74.01(1.1)"])
    assert r["sections"][0]["headnote"] == "Drip pricing", r["sections"][0]["headnote"]
    assert "by or under an Act of Parliament or the legislature of a province" in text_of(r)


# ---------------------------------------------------------------- offline tests (no network, fixed data)
import time  # noqa: E402

CUR = ["November 18, 2026", "test fixture"]


def elaws_page(title, paras):
    body = "".join(f'<p class="{c}">{t}</p>' for c, t in paras)
    return (f'<html><head><title>{title} | Ontario.ca</title><meta property="og:description" content="x"></head><body>'
            f'<p class="ConsolidationPeriod-e">Consolidation Period: From January 1, 2026 to the e-Laws currency date.</p>'
            f'{body}</body></html>')


def offline(page, src="on", code="02c30", targets=(), cur=None, **kw):
    saved = law.fetch_paired
    law.fetch_paired = lambda url, kind, fn, fresh=False: (page, time.time(), True, cur if cur is not None else
                                                           (list(CUR) if src == "on" else
                                                            "Act current to 2026-10-29 and last amended on 2026-01-01"))
    try:
        return law.lookup(src, code, list(targets), orders=False, **kw)
    finally:
        law.fetch_paired = saved


ON_PAGE = elaws_page("Test Act, 2026, S.O. 2026, c. 99", [
    ("section-e", "1 In this Act,"),
    ("definition-e", "“wages” means,"),
    ("defclause-e", "(a) money, and"),
    ("definition-e", "but does not include,"),
    ("defclause-e", "(b) tips; (“salaire”)"),
    ("Pnote-e", "Note: On a day to be named by proclamation of the Lieutenant Governor, section 1 of the Act is amended "
                "by adding the following definition: (See: 2026, c. 99, s. 50)"),
    ("Ydefinition-e", "“penalty” means a penalty imposed under section 75.1; (“pénalité”)"),
    ("definition-e", "“spouse” means either of two persons who,"),
    ("defclause-e", "(a) are married,"),
    ("Sparagraph-e", "unless they live apart; (“conjoint”)"),
    ("headnote-e", "Immunity"),
    ("section-e", "<strong>6</strong><strong>9 </strong>No action lies. 2026, c. 99, s. 69."),
    ("headnote-e", "Payment"),
    ("section-e", "70 (1) A payment is due. 2026, c. 99, s. 70 (1)."),
    ("Pnote-e", "Note: On a day to be named by proclamation of the Lieutenant Governor, section 70 of the Act is repealed. "
                "(See: 2026, c. 99, s. 51)"),
    ("headnote-e", "Same"),
    ("subsection-e", "(2) A second rule applies. 2026, c. 99, s. 70 (2)."),
    ("section-e", "71.-73 Repealed: 2020, c. 1, s. 2."),
    ("section-e", "74., 74.1 Repealed: 2020, c. 1, s. 3."),
    ("section-e", "75 The amount is A = B<sup>-N</sup>. 2026, c. 99, s. 75."),
    ("Pnote-e", "Note: On a day to be named by proclamation of the Lieutenant Governor, the Act is amended by adding the "
                "following Part: (See: 2026, c. 99, s. 52)"),
    ("Ypartnum-e", "PART X.1 PENALTIES"),
    ("Yheadnote-e", "Order"),
    ("Ysection-e", "75.1 The Director may impose a penalty. 2026, c. 99, s. 52."),
    ("Yheadnote-e", "Appeal"),
    ("Ysection-e", "75.2 A person may appeal a penalty. 2026, c. 99, s. 52."),
    ("headnote-e", "Fees"),
    ("section-e", "76 Fees may be charged. 2026, c. 99, s. 76."),
    ("Pnote-e", "Note: On the day section 3 of Schedule 2 to the Other Act, 2018 comes into force, section 76 of the Act "
                "is amended by adding “and penalties”. (See: 2018, c. 8, Sched. 37, s. 3)"),
    ("schedule-e", "Schedule 1 Categories"),
    ("headnote-e", "Lighting"),
    ("section-e", "1. Lighting includes lamps."),
    ("section-e", "2. Tools include drills."),
])


def flags_for(r, where):
    return [f for f in r["flags"] if f["where"] == where]


def t_off_clean_inline_tags():
    assert law.clean("<strong>6</strong><strong>9 </strong>No action") == "69 No action"
    assert law.clean("FV(1 + I)<sup>-N</sup>") == "FV(1 + I)^(-N)"
    assert law.clean("<i>i</i>f a borrower") == "if a borrower"


def t_off_define_whole_tail():
    r = offline(ON_PAGE, define="wages")
    t = "\n".join(x["text"] for x in r["definitions"][0]["rows"])
    assert "but does not include," in t and "(b) tips;" in t, t
    r = offline(ON_PAGE, define="spouse")
    t = "\n".join(x["text"] for x in r["definitions"][0]["rows"])
    assert t.endswith("unless they live apart; (“conjoint”)"), t
    assert "penalty" not in t, "the next definition must not be pulled in"


def t_off_define_wholly_not_in_force():
    r = offline(ON_PAGE, define="penalty")
    d = r["definitions"][0]
    assert d["rows"][0]["kind"] == "note" and "adding the following definition" in d["rows"][0]["text"], d["rows"]
    assert d.get("only_pending") and law.exit_code(r) == 5, (d, law.exit_code(r))
    assert any("whole definition is not yet in force" in f["text"] for f in r["flags"]), r["flags"]
    r = offline(ON_PAGE, define="wages")
    assert not any(x["kind"] == "note" for x in r["definitions"][0]["rows"]), "the note belongs to the next definition"


def t_off_split_section_number():
    r = offline(ON_PAGE, targets=["69", "6"])
    assert r["sections"][0]["found"] and text_of(r).startswith("69 No action lies."), r["sections"][0]
    assert r["sections"][0]["headnote"] == "Immunity"
    assert not r["sections"][1]["found"] and law.exit_code(r) == 4


def t_off_subsection_keeps_section_note():
    r = offline(ON_PAGE, targets=["70(2)"])
    rows = r["sections"][0]["rows"]
    assert any(x["kind"] == "note" and x.get("elsewhere") and "section 70 of the Act is repealed" in x["text"] for x in rows), rows
    assert any(f["level"] == "PENDING" and f["text"].startswith("Elsewhere in this section") for f in r["flags"]), r["flags"]
    assert "(2) A second rule applies." in text_of(r)


def t_off_repealed_range_and_list():
    r = offline(ON_PAGE, targets=["72", "74.1", "70"])
    assert "71.-73 Repealed" in text_of(r, 0) and "74., 74.1 Repealed" in text_of(r, 1), (text_of(r, 0), text_of(r, 1))
    assert [f["level"] for f in flags_for(r, "s. 72")].count("REPEALED") == 1, flags_for(r, "s. 72")
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 74.1")), flags_for(r, "s. 74.1")
    assert "Repealed" not in text_of(r, 2), "a repealed run must not be filed under the section before it"
    assert law.exit_code(r) == 5


def t_off_formula_exponent():
    r = offline(ON_PAGE, targets=["75"])
    assert "A = B^(-N)" in text_of(r), text_of(r)
    assert not any(x["kind"] == "note" for x in r["sections"][0]["rows"]), "the Part note belongs to s. 75.1"


def t_off_part_note_carried():
    r = offline(ON_PAGE, targets=["75.1", "75.2", "76"])
    e1, e2, e3 = r["sections"]
    assert e1["context"] == ["[NOT YET IN FORCE] PART X.1 PENALTIES"], e1["context"]
    assert e1["headnote"] == "Order" and e2["headnote"] == "Appeal", (e1["headnote"], e2["headnote"])
    for e in (e1, e2):
        assert e["rows"][0]["kind"] == "note" and "adding the following Part" in e["rows"][0]["text"], e["rows"]
        assert e.get("only_pending"), e
    assert not any("adding the following Part" in x["text"] for x in e3["rows"]), e3["rows"]
    assert any(f["level"] == "CHECK" and "section 3 of Schedule 2 to the Other Act, 2018" in f["text"]
               for f in flags_for(r, "s. 76")), flags_for(r, "s. 76")


def t_off_schedule_keys():
    r = offline(ON_PAGE, targets=["1", "sch1", "sch1:2", "1:1"])
    assert "Lighting includes lamps" not in text_of(r, 0), text_of(r, 0)
    assert "1. Lighting includes lamps." in text_of(r, 1) and "2. Tools include drills." in text_of(r, 1), text_of(r, 1)
    assert text_of(r, 2) == "2. Tools include drills.", text_of(r, 2)
    assert r["sections"][3]["headnote"] == "Lighting" and text_of(r, 3) == "1. Lighting includes lamps.", r["sections"][3]


def t_off_find_tags():
    r = offline(ON_PAGE, find="and penalties")
    h = r["matches"][0]
    assert h["section"] == "76" and h["only_in_note"] and not h["not_in_force"], h
    r = offline(ON_PAGE, find="may impose a penalty")
    h = r["matches"][0]
    assert h["section"] == "75.1" and h["not_in_force"], h


FED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Statute xmlns:lims="http://justice.gc.ca/lims" lims:pit-date="2026-10-29" lims:current-date="2026-10-29" in-force="yes">
<Identification><ShortTitle>Test Act</ShortTitle></Identification>
<Body>
<Section><MarginalNote>Definitions</MarginalNote><Label>2</Label><Subsection><Label>(1)</Label><Text>The following definitions apply.</Text>
<Definition><Text><DefinedTermEn>widget</DefinedTermEn> means a small device.</Text></Definition></Subsection></Section>
<Section><MarginalNote>Rate</MarginalNote><Label>9</Label><Text>The rate is set out in Schedule I.</Text></Section>
<Section><MarginalNote>Formula</MarginalNote><Label>10</Label><Text>The amount is determined by the formula</Text>
<FormulaGroup><Formula><FormulaText>A – B</FormulaText></Formula><FormulaConnector>where</FormulaConnector>
<FormulaDefinition><FormulaTerm>B</FormulaTerm><Text>is the lesser of</Text><FormulaParagraph><Label>(i)</Label><Text>one, and</Text>
</FormulaParagraph></FormulaDefinition></FormulaGroup></Section>
</Body>
<Schedule><ScheduleFormHeading><Label>SCHEDULE I</Label><TitleText>Rates</TitleText></ScheduleFormHeading><BillPiece>
<Section><Label>1</Label><Text>Gas $1.</Text></Section>
<Section><Label>3. and 4</Label><Text><Repealed>[Repealed, 2000, c. 1, s. 2]</Repealed></Text></Section></BillPiece></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE II.1</Label><TitleText>Other Rates</TitleText></ScheduleFormHeading><BillPiece>
<Section><Label>1</Label><Text>Diesel $2.</Text></Section></BillPiece></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE V</Label><TitleText>Exempt Supplies</TitleText></ScheduleFormHeading><DocumentInternal>
<Group><GroupHeading><Label>PART I</Label><TitleText>Real Property</TitleText></GroupHeading>
<Provision><Label>1</Label><Text><Repealed>[Repealed, 1997, c. 10, s. 85]</Repealed></Text></Provision>
<Provision><Label>2</Label><Text>A sale of land</Text><Provision><Label>(a)</Label><Text>if old, or</Text></Provision></Provision></Group>
<Group><GroupHeading><Label>PART II</Label><TitleText>Health</TitleText></GroupHeading>
<Provision><Label>1</Label><Text>In this Part, care means care.</Text></Provision></Group></DocumentInternal></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE</Label><TitleText>List of States</TitleText></ScheduleFormHeading>
<List><Item><Text>Albania</Text></Item><Item><Text>Australia</Text></Item></List></Schedule>
<Schedule><ScheduleFormHeading><TitleText>AMENDMENTS NOT IN FORCE</TitleText></ScheduleFormHeading><BillPiece>
<RelatedOrNotInForce><Heading>— 2026, c. 25, s. 3(1)</Heading><Section><Label>3</Label><Subsection><Label>(1)</Label>
<Text>Schedule I to the Act is amended by adding the following after section 1:</Text>
<AmendedText><Section><Label>2</Label><Text>Fuel $3, as defined in section 9.</Text></Section></AmendedText></Subsection></Section></RelatedOrNotInForce>
<RelatedOrNotInForce><Heading>— 2026, c. 3, s. 94(2)</Heading><Section><Label>94</Label><Subsection><Label>(2)</Label>
<Text>The definition <DefinedTermEn>widget</DefinedTermEn> in subsection 2(1) of the Act is replaced by the following:</Text>
</Subsection></Section></RelatedOrNotInForce></BillPiece></Schedule>
</Statute>"""


def t_off_fed_schedule_content():
    r = offline(FED_XML, src="ca", code="T-1", targets=["sch", "schII.1", "schI", "schV"])
    assert "Albania" in text_of(r, 0) and "Australia" in text_of(r, 0), text_of(r, 0)
    assert "Diesel $2." in text_of(r, 1) and "Gas" not in text_of(r, 1), text_of(r, 1)
    assert "Diesel" not in text_of(r, 2), "schedules II and II.1 must not merge"
    t = text_of(r, 3)
    assert "2 A sale of land" in t and "(a) if old, or" in t, t
    assert not any(f["level"] == "REPEALED" for f in flags_for(r, "Sch. V")), flags_for(r, "Sch. V")
    r = offline(FED_XML, src="ca", code="T-1", find="Australia")
    assert r["matches"] and r["matches"][0]["section"] == "sch", r["matches"]


def t_off_fed_schedule_parts_and_lists():
    r = offline(FED_XML, src="ca", code="T-1", targets=["schV:II:1"])
    assert text_of(r) == "1 In this Part, care means care.", text_of(r)
    r = offline(FED_XML, src="ca", code="T-1", targets=["schV:1"])
    assert any("separately in each Part" in f["text"] for f in r["flags"]), r["flags"]
    assert any("In Part I this item is repealed" in f["text"] for f in r["flags"]), r["flags"]
    assert law.exit_code(r) == 0
    r = offline(FED_XML, src="ca", code="T-1", targets=["schI:4"])
    assert "3. and 4 [Repealed" in text_of(r) and law.exit_code(r) == 5, (text_of(r), r["flags"])


def t_off_fed_pending_schedule_vs_body():
    r = offline(FED_XML, src="ca", code="T-1", targets=["9", "schI", "schI:2"])
    assert not any(f["level"] == "PENDING" for f in flags_for(r, "s. 9")), flags_for(r, "s. 9")
    assert any(f["level"] == "PENDING" for f in flags_for(r, "Sch. I")), flags_for(r, "Sch. I")
    assert r["sections"][2].get("only_pending"), r["sections"][2]


def t_off_fed_formula_lines():
    t = text_of(offline(FED_XML, src="ca", code="T-1", targets=["10"]))
    for line in ("A – B", "where", "B is the lesser of", "(i) one, and"):
        assert line in t.split("\n"), (line, t)


def t_off_fed_define_pending():
    r = offline(FED_XML, src="ca", code="T-1", define="widget")
    assert r["definitions"] and "widget means a small device." in r["definitions"][0]["rows"][0]["text"], r["definitions"]
    assert any(f["level"] == "PENDING" and "names this term" in f["text"] for f in r["flags"]), r["flags"]


def _fake_orders(orders):
    return lambda code, fresh=False: {"act_code": code, "act": "Amending Act, 2025, S.O. 2025, c. 24", "url": "u",
                                      "listed": len(orders), "orders": orders}


def _order(code, ordered, named=(), text=("1. Something comes into force.",)):
    return {"code": code, "title": "ORDER IN COUNCIL " + code, "ordered": ordered, "named_days": list(named),
            "text": list(text), "url": "https://www.ontario.ca/laws/oic/" + code}


def _orders_res():
    return {"flags": [{"level": "PENDING", "where": "s. 1", "text": "Note: On a day to be named by order of the Lieutenant "
                       "Governor in Council, ... (See: 2025, c. 24, Sched. 11, s. 15)"}],
            "status": "IN FORCE", "title": "Test Act, 2002, S.O. 2002, c. 33", "sections": [], "definitions": None,
            "header_notes": []}


def t_off_order_dates_against_currency():
    saved = law.on_orders
    law.on_orders = _fake_orders([_order("o1", "November 19, 2026"), _order("o2", "November 18, 2026"),
                                  _order("o3", "November 1, 2026"),
                                  _order("o4", "November 1, 2026", named=["November 19, 2026"])])
    try:
        r = _orders_res()
        law.attach_orders(r, None, "November 18, 2026")
        txt = [f["text"] for f in r["flags"] if f["level"] == "CHECK"]
        assert any("o1" in t and "after the e-Laws currency date" in t for t in txt), txt
        assert any("o2" in t and "on the e-Laws currency date" in t for t in txt), txt
        assert not any("/o3" in t for t in txt), txt
        assert any("/o4" in t and "in force now" in t for t in txt), txt
        r = _orders_res()
        law.attach_orders(r, None, None)
        txt = [f["text"] for f in r["flags"] if f["level"] == "CHECK"]
        for c in ("o1", "o2", "o3", "o4"):
            assert any("/" + c in t and "could not be read" in t for t in txt), (c, txt)
    finally:
        law.on_orders = saved


def t_off_contingent_note_checks_orders():
    saved, seen = law.on_orders, []
    law.on_orders = lambda code, fresh=False: seen.append(code) or _fake_orders([])(code)
    try:
        r = _orders_res()
        r["flags"][0]["text"] = ("Note: On the day section 3 of Schedule 2 to the Other Act, 2018 comes into force, ... "
                                 "(See: 2018, c. 8, Sched. 37, s. 3)")
        law.attach_orders(r, None, "November 18, 2026")
        assert seen == ["s18008"], seen
    finally:
        law.on_orders = saved


ON_PAGE2 = elaws_page("Second Test Act, 2026, S.O. 2026, c. 98", [
    ("section-e", "1 In this Act,"),
    ("definition-e", "“device” means a computer; (“appareil”)"),
    ("Pnote-e", "Note: On a day to be named by proclamation of the Lieutenant Governor, section 1 of the Act is repealed. "
                "(See: 2026, c. 98, s. 90)"),
    ("headnote-e", "Duties"),
    ("section-e", "2 (1) Repealed: 2026, c. 98, s. 2."),
    ("headnote-e", "Same"),
    ("subsection-e", "(2) A supplier shall keep records. 2026, c. 98, s. 2 (2)."),
    ("headnote-e", "Notice"),
    ("section-e", "3 (1) A supplier shall give notice. 2026, c. 98, s. 3 (1)."),
    ("subsection-e", "(5) Notice may be by email. 2026, c. 98, s. 3 (5)."),
    ("Pnote-e", "Note: On a day to be named by proclamation of the Lieutenant Governor, subsection 3 (5) of the Act is repealed "
                "and the following substituted: (See: 2026, c. 98, s. 91)"),
    ("Ysubsection-e", "(5) Notice must be in writing."),
    ("Ysubsection-e", "(6) A copy must be kept."),
    ("headnote-e", "Meaning of “establishment”"),
    ("section-e", "4 (1) For the purposes of this Part, “device” includes a tablet. 2026, c. 98, s. 4."),
    ("Pnote-e", "Note: On the later of the day section 3 of Schedule 1 to the First Act, 2025 comes into force and the day "
                "section 7 of the Second Act, 2026 comes into force, section 4 of the Act is amended. (See: 2026, c. 98, s. 92)"),
])


def t_off_partly_repealed_section_is_in_force():
    r = offline(ON_PAGE2, code="02c30", targets=["2", "2(1)"])
    assert law.exit_code({"sections": [r["sections"][0]], "flags": [f for f in r["flags"] if f["where"] == "s. 2"],
                          "status": r["status"]}) == 0, r["flags"]
    assert any(f["level"] == "CHECK" and "Subsection (1) of this section is repealed" in f["text"] for f in flags_for(r, "s. 2"))
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 2(1)")), flags_for(r, "s. 2(1)")


def t_off_subsection_brought_in_by_other_note():
    r = offline(ON_PAGE2, code="02c30", targets=["3(6)", "3(1)"])
    rows = r["sections"][0]["rows"]
    assert any(x.get("elsewhere") and "subsection 3 (5) of the Act is repealed" in x["text"] for x in rows), rows
    assert not any("subsection 3 (5)" in x["text"] for x in r["sections"][1]["rows"]), "a note on (5) is not about (1)"


def t_off_define_section_notes_and_own_meaning():
    r = offline(ON_PAGE2, code="02c30", define="device")
    d = r["definitions"]
    assert d[0]["section"] == "1" and any("section 1 of the Act is repealed" in x["text"] for x in d[0]["rows"]), d[0]["rows"]
    assert any(f["level"] == "PENDING" and "section 1 of the Act is repealed" in f["text"] for f in flags_for(r, "s. 1"))
    assert len(d) == 2 and d[1]["section"] == "4" and d[1].get("own_meaning"), d
    assert any("later of the days" in f["text"] and "Second Act, 2026" in f["text"] for f in r["flags"]), r["flags"]
    r = offline(ON_PAGE2, code="02c30", define="no such term")
    assert law.exit_code(r) == 4


FED_XML2 = """<?xml version="1.0" encoding="UTF-8"?>
<Statute xmlns:lims="http://justice.gc.ca/lims" lims:pit-date="2026-10-29" lims:current-date="2026-10-29" in-force="yes">
<Identification><ShortTitle>Second Test Act</ShortTitle></Identification>
<Body>
<Section><MarginalNote>Definitions</MarginalNote><Label>2</Label><Text>In this Act,</Text>
<Definition><Text><DefinedTermEn>gadget</DefinedTermEn><Repealed>[Repealed, 2000, c. 1, s. 1]</Repealed></Text></Definition></Section>
<Section><MarginalNote>Review</MarginalNote><Label><FootnoteRef idref="x">*</FootnoteRef>29</Label><Text>A committee shall review this Act.</Text></Section>
</Body>
<Schedule><ScheduleFormHeading><Label>SCHEDULE II</Label><TitleText>Old</TitleText></ScheduleFormHeading>
<Provision><Text><Repealed>[Repealed, 2002, c. 22, s. 390]</Repealed></Text></Provision></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE I</Label><OriginatingRef>(Section 9)</OriginatingRef><TitleText>Rates</TitleText>
</ScheduleFormHeading><BillPiece>
<Section><Label>1</Label><Text>Gas $1.</Text></Section>
<Section><Label>5 to 5.2</Label><Text><Repealed>[Repealed, 2006, c. 4, s. 89]</Repealed></Text></Section></BillPiece></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE V</Label><TitleText>Exempt Supplies</TitleText></ScheduleFormHeading><DocumentInternal>
<Group><GroupHeading><Label>PART V</Label><TitleText>Legal Aid</TitleText></GroupHeading>
<Provision><Label>1</Label><Text>Legal aid services.</Text></Provision></Group>
<Group><GroupHeading><Label>PART V.1</Label><TitleText>Charities</TitleText></GroupHeading>
<Provision><Label>1</Label><Text>A supply by a charity.</Text></Provision></Group></DocumentInternal></Schedule>
<Schedule><ScheduleFormHeading><TitleText>AMENDMENTS NOT IN FORCE</TitleText></ScheduleFormHeading><BillPiece>
<RelatedOrNotInForce><Heading>— 2026, c. 25, s. 3(1)</Heading><Section><Label>3</Label><Subsection><Label>(1)</Label>
<Text>Schedule I to the Act is amended by adding the following after section 9.2:</Text>
<AmendedText><Section><Label>9.3</Label><Text>Fuel $3.</Text></Section></AmendedText></Subsection></Section></RelatedOrNotInForce>
<RelatedOrNotInForce><Heading>— 2026, c. 3, s. 94(14)</Heading><Section><Label>94</Label><Subsection><Label>(14)</Label>
<Text>Subsection 2(1) of the Act is amended by adding the following in alphabetical order:</Text>
<AmendedText><Definition><Text><DefinedTermEn>data mobility</DefinedTermEn> means moving data.</Text></Definition></AmendedText>
</Subsection></Section></RelatedOrNotInForce></BillPiece></Schedule>
</Statute>"""


def t_off_fed_footnoted_label_and_parts():
    r = offline(FED_XML2, src="ca", code="T-2", targets=["29", "schV:V:1", "schV:V.1:1", "schV:V.1"])
    assert r["sections"][0]["found"] and "A committee shall review" in text_of(r, 0), r["sections"][0]
    assert text_of(r, 1) == "1 Legal aid services.", text_of(r, 1)
    assert text_of(r, 2) == "1 A supply by a charity.", text_of(r, 2)
    assert "PART V.1 Charities" in text_of(r, 3) and "Legal aid" not in text_of(r, 3), text_of(r, 3)


def t_off_fed_schedule_repeal_scope():
    r = offline(FED_XML2, src="ca", code="T-2", targets=["schII"])
    assert any(f["level"] == "REPEALED" for f in r["flags"]) and law.exit_code(r) == 5, r["flags"]
    r = offline(FED_XML2, src="ca", code="T-2", targets=["schI"])
    assert law.exit_code(r) == 0 and "(Section 9)" in r["sections"][0]["rows"][0]["text"], (r["flags"], r["sections"][0]["rows"][:1])
    r = offline(FED_XML2, src="ca", code="T-2", targets=["schI:5.1", "schI:1"])
    assert "5 to 5.2 [Repealed" in text_of(r, 0) and any(f["level"] == "REPEALED" for f in flags_for(r, "Sch. I, 5.1"))
    assert not any(f["level"] == "PENDING" for f in flags_for(r, "Sch. I, 1")), "a change to item 9.3 is not a change to item 1"
    assert any(f["level"] == "CHECK" and "does not name item 1" in f["text"] for f in flags_for(r, "Sch. I, 1"))


def t_off_fed_repealed_and_pending_definitions():
    r = offline(FED_XML2, src="ca", code="T-2", define="gadget")
    assert any(f["level"] == "REPEALED" for f in r["flags"]) and law.exit_code(r) == 5, r["flags"]
    assert "gadget [Repealed" in r["definitions"][0]["rows"][0]["text"], r["definitions"][0]["rows"]
    r = offline(FED_XML2, src="ca", code="T-2", define="data mobility")
    assert r["pending_only"] and law.exit_code(r) == 5, r["flags"]
    r = offline(FED_XML2, src="ca", code="T-2", find="moving data")
    assert r["matches"] and r["matches"][0]["not_in_force"], r["matches"]


def t_live_partly_repealed_sections():
    r = run("on", "04p03", ["47"], orders=False)
    assert law.exit_code(r) == 0 and not any(f["level"] == "REPEALED" for f in r["flags"]), r["flags"]
    r = run("ca", "I-3.3", ["24"])
    assert not any(f["level"] == "REPEALED" for f in flags_for(r, "s. 24")), flags_for(r, "s. 24")


def t_live_fed_footnote_label_and_repealed_schedule():
    r = run("ca", "P-8.6", ["29"])
    assert r["sections"][0]["found"], "PIPEDA s. 29 has a footnote marker in its label"
    r = run("ca", "E-15", ["schII", "schV:V.1:1"])
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "Sch. II")), r["flags"]
    assert "charity" in text_of(r, 1), text_of(r, 1)[:200]


def t_live_definition_keeps_section_notes():
    r = run("on", "17c14", define="registered person", orders=False)
    assert any(f["level"] == "PENDING" and "section 133 of the Act is repealed" in f["text"] for f in r["flags"]), r["flags"]


def t_live_subsection_note_orders():
    r = run("on", "02f33", ["3(6)"])
    assert any(i["act_code"] == "s25024" for i in r.get("commencement_orders") or []), "orders for 2025, c. 24 not checked"


# ---------------------------------------------------------------- live checks for the audit's real-data cases
def t_live_esa_wages_whole():
    r = run("on", "00e41", define="wages")
    t = "\n".join(x["text"] for x in r["definitions"][0]["rows"])
    assert "but does not include," in t and "(d) tips or other gratuities," in t, t[-400:]


def t_live_phipa_spouse_and_69():
    r = run("on", "04p03", define="spouse")
    assert "separate and apart" in "\n".join(x["text"] for x in r["definitions"][0]["rows"])
    r = run("on", "04p03", ["69"])
    assert text_of(r).startswith("69 No action"), text_of(r)[:100]


def t_live_reg522_schedule():
    r = run("on", "r:200522", ["2", "sch1"])
    assert "Lighting" not in text_of(r, 0), text_of(r, 0)[-300:]
    assert "Computers." in text_of(r, 1), text_of(r, 1)[:300]


def t_live_fbcsa_73_5_wholly_pending():
    r = run("on", "02f33", ["73.5", "75"], orders=False)
    assert r["sections"][0].get("only_pending"), r["sections"][0]
    assert "74.-76" not in text_of(r, 0), "the repealed run must not be filed under s. 73.5"
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 75")), r["flags"]


def t_live_cpa_part_note_on_new_part():
    r = run("on", "02c30", ["104", "104.0.1"], orders=False)
    assert not any("adding the following Part" in x["text"] for x in r["sections"][0]["rows"]), "s. 104 must not get the note"
    assert any("adding the following Part" in x["text"] for x in r["sections"][1]["rows"]), r["sections"][1]["rows"][:2]


def t_live_esa_8_2_1_section_note():
    r = run("on", "00e41", ["8.2(1)"], orders=False)
    assert any(x.get("elsewhere") and "section 8.2 of the Act is repealed" in x["text"] for x in r["sections"][0]["rows"])


def t_live_cpa_find_119_tag():
    r = run("on", "02c30", find="or administrative penalty", orders=False)
    h = next(h for h in r["matches"] if h["section"] == "119")
    assert h["not_in_force"], h


def t_live_wsia_contingent_check():
    r = run("on", "97w16", ["14(2)"], orders=False)
    assert any(f["level"] == "CHECK" and "Correctional Services Transformation Act, 2018" in f["text"] for f in r["flags"])


def t_live_fed_schedules():
    r = run("ca", "SOR/2013-221", ["sch"])
    assert "Australia" in text_of(r), "list items missing"
    r = run("ca", "E-15", ["schV", "schII.1", "9", "schI"])
    assert law.exit_code(r) == 0 or not any(f["level"] == "REPEALED" for f in flags_for(r, "Sch. V")), r["flags"]
    assert "Specific Tax Rates on Petroleum Products" in text_of(r, 1)
    assert not any(f["level"] == "PENDING" and "Schedule I to the Act" in f["text"] for f in flags_for(r, "s. 9"))
    assert any(f["level"] == "PENDING" for f in flags_for(r, "Sch. I")), flags_for(r, "Sch. I")


def t_live_pipeda_schedule_note_and_labels():
    r = run("ca", "P-8.6", ["sch1:4.3", "sch1:4.3.7"])
    assert "Note: In certain circumstances personal information can be collected" in text_of(r, 0)
    assert "(a) an application form" in text_of(r, 1), text_of(r, 1)[:300]


def t_live_ita_define_pending():
    r = run("ca", "I-3.3", define="superannuation or pension benefit")
    assert any(f["level"] == "PENDING" for f in r["flags"]), r["flags"]


def t_off_cite_forms():
    want = {"43": "s. 43", "43(2)(a)": "s. 43(2)(a)", "sch1": "Sch. 1", "sch1:4.1.3": "Sch. 1, 4.1.3",
            "schV:II:2": "Sch. V, Part II, 2", "schVI:VIII": "Sch. VI, Part VIII", "1:110": "Sch. 1, 110",
            "schform5.1": "Form 5.1", "schII.1": "Sch. II.1", "sch1:2(1)(a)": "Sch. 1, 2(1)(a)", "?": "s. ?"}
    got = {k: law.cite(k) for k in want}
    assert got == want, got
    r = law.format_text({"url": "u", "title": "t", "status": "IN FORCE", "fetched": "f", "tool_version": "v", "flags": [],
                         "sections": [{"target": "sch1:4.1.3", "found": True, "headnote": None, "context": [],
                                       "rows": [], "history": []},
                                      {"target": "schI:9", "found": False}]})
    assert "== Sch. 1, 4.1.3 ==" in r and "[Sch. I, 9 not found]" in r, r


def t_off_define_alternative_name():
    page = elaws_page("Alt Reg", [
        ("section-e", "1. In this Regulation,"),
        ("definition-e", "“information technology, telecommunications and audio visual equipment” or “ITT/AV” means EEE "
                         "set out in Schedule 1; (“ITT/AV”)"),
        ("definition-e", "“lighting” means EEE set out in Schedule 2; (“éclairage”)")])
    for q in ("ITT/AV", "information technology, telecommunications and audio visual equipment"):
        r = offline(page, code="r:200522", define=q)
        t = "\n".join(x["text"] for x in r["definitions"][0]["rows"])
        assert "set out in Schedule 1" in t and "lighting" not in t, (q, t)
    assert law.def_terms('"a" or "b" means c') == ["a", "b"] and law.def_terms('"a", in relation to b, means') == ["a"]
    assert law.def_terms('"a", "b" and "c" mean') == ["a", "b", "c"] and law.def_terms("no quote") == []


ON_PAGE_A4 = elaws_page("Fourth Test Act, 2026, S.O. 2026, c. 97", [
    ("section-e", "<strong>69 </strong>Repealed: 2025, c. 24, Sched. 11, s. 12."),
    ("headnote-e", "Search warrant"),
    ("subsection-e", '<a name="BK77"></a><strong>70 </strong>(1) Upon application, a justice of the peace may issue a warrant. '
                     "2026, c. 97, s. 70 (1)."),
    ("subsection-e", "(2) The warrant names the place. 2026, c. 97, s. 70 (2)."),
    ("headnote-e", "Powers"),
    ("section-e", "<strong>71 </strong>(1) An investigator may act without a warrant. 2026, c. 97, s. 71 (1)."),
    ("subsection-e", "(2) See section 10 of the Act. 2026, c. 97, s. 71 (2)."),
])


def t_off_subsection_row_starts_new_section():
    r = offline(ON_PAGE_A4, targets=["70", "69", "69(1)", "71"])
    s70, s69, s69_1, s71 = r["sections"]
    assert s70["found"] and s70["headnote"] == "Search warrant" and "70 (1) Upon application" in text_of(r, 0), s70
    assert "(2) The warrant names the place" in text_of(r, 0)
    assert "70 (1)" not in text_of(r, 1) and any(f["level"] == "REPEALED" for f in flags_for(r, "s. 69")), r["flags"]
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 69(1)")), r["flags"]
    assert "70 (1)" not in text_of(r, 3) and "An investigator" in text_of(r, 3)
    r = offline(ON_PAGE_A4, targets=["69"])
    assert law.exit_code(r) == 5, r["flags"]


FED_XML_A4 = """<?xml version="1.0" encoding="UTF-8"?>
<Statute xmlns:lims="http://justice.gc.ca/lims" lims:pit-date="2026-10-29" lims:current-date="2026-10-29" in-force="yes">
<Identification><ShortTitle>Range Act</ShortTitle></Identification>
<Body>
<Section><Label>12</Label><Text>Live text.</Text></Section>
<Section><Label>594 to 596</Label><Text><Repealed>[Repealed, R.S., 1985, c. 27 (1st Supp.), s. 120]</Repealed></Text></Section>
<Section><Label>614 to 619</Label><Text><Repealed>[ Repealed, 1991, c. 43, s. 3]</Repealed></Text></Section>
<Section><Label>70.61 to 70.8</Label><Text><Repealed>[Repealed, 1997, c. 24, s. 50]</Repealed></Text></Section>
<Section><MarginalNote>Review</MarginalNote><Label><FootnoteRef idref="f1">*</FootnoteRef>29</Label><Subsection><Label>(1)</Label>
<Text>The administration of this Part shall be reviewed.</Text></Subsection></Section>
<Section><Label><FootnoteRef idref="f2">*</FootnoteRef>31</Label><Text><Repealed>[Repealed, 2001, c. 1, s. 2]</Repealed></Text></Section>
</Body>
<Schedule><ScheduleFormHeading><Label>SCHEDULE V</Label><TitleText>Exempt Supplies</TitleText></ScheduleFormHeading><DocumentInternal>
<Group><GroupHeading><Label>PART II</Label><TitleText>Health</TitleText></GroupHeading>
<Provision><Label>1</Label><Text>In this Part,</Text>
<Provision><Text><DefinedTermEn>cosmetic service supply</DefinedTermEn> means a supply made for cosmetic purposes;</Text></Provision>
<Provision><Text><DefinedTermEn>health care facility</DefinedTermEn> means</Text>
<Provision><Label>(a)</Label><Text>a hospital, or</Text></Provision><Provision><Label>(b)</Label><Text>a clinic;</Text></Provision></Provision>
</Provision></Group>
<Group><GroupHeading><Label>PART III</Label><TitleText>Education</TitleText></GroupHeading>
<Provision><Label>1</Label><Text>In this Part, school means a school.</Text></Provision></Group></DocumentInternal></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE [to Part XXV]</Label><OriginatingRef>(Section 762)</OriginatingRef></ScheduleFormHeading>
<BillPiece><Section><Label>1</Label><Text>Twenty-five.</Text></Section></BillPiece></Schedule>
<Schedule><ScheduleFormHeading><Label>SCHEDULE [to Part XXVII]</Label><OriginatingRef>(Section 840)</OriginatingRef></ScheduleFormHeading>
<BillPiece><Section><Label>1</Label><Text>Twenty-seven.</Text></Section></BillPiece></Schedule>
<Schedule><ScheduleFormHeading><TitleText>AMENDMENTS NOT IN FORCE</TitleText></ScheduleFormHeading><BillPiece>
<RelatedOrNotInForce><Heading>— 2026, c. 3, s. 389</Heading><Section><Label>389</Label>
<Text>The Act is amended by adding the following after section 12:</Text>
<AmendedText><Section><Label>12.1</Label><Text>A right to data mobility applies.</Text></Section></AmendedText></Section></RelatedOrNotInForce>
</BillPiece></Schedule>
</Statute>"""


def t_off_fed_range_labels_and_footnotes():
    r = offline(FED_XML_A4, src="ca", code="C-99", targets=["595", "615", "70.7", "70.62", "29", "31", "12"])
    for i, t in enumerate(["595", "615", "70.7", "70.62"]):
        assert r["sections"][i]["found"], t
        assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. " + t)), (t, r["flags"])
    assert any("Shown under the range 594-596" in f["text"] for f in flags_for(r, "s. 595"))
    assert text_of(r, 4).startswith("29 (1) The administration"), text_of(r, 4)
    assert "*" not in text_of(r, 4) and any(f["level"] == "REPEALED" for f in flags_for(r, "s. 31")), r["flags"]
    assert not any(f["level"] == "REPEALED" for f in flags_for(r, "s. 12"))
    assert law.exit_code(offline(FED_XML_A4, src="ca", code="C-99", targets=["595"])) == 5


def t_off_fed_schedule_definitions_find_and_keys():
    r = offline(FED_XML_A4, src="ca", code="C-99", define="health care facility")
    d = r["definitions"]
    assert len(d) == 1 and d[0]["label"] == "Sch. V, Part II, 1", d
    t = "\n".join(x["text"] for x in d[0]["rows"])
    assert "(b) a clinic;" in t and "cosmetic" not in t, t
    assert law.exit_code(r) == 0
    r = offline(FED_XML_A4, src="ca", code="C-99", find="In this Part,")
    assert [h["section"] for h in r["matches"]] == ["schv:ii:1", "schv:iii:1"], r["matches"]
    assert "Sch. V, Part III, 1: ..." in law.format_text(r)
    r = offline(FED_XML_A4, src="ca", code="C-99", targets=["schpartxxv", "Sch. to Part XXVII"])
    assert "Twenty-five." in text_of(r, 0) and "Twenty-seven." not in text_of(r, 0)
    assert "Twenty-seven." in text_of(r, 1) and not any("Two schedules" in f["text"] for f in r["flags"]), r["flags"]
    assert law.fed_sched_key("SCHEDULE [to Part XXV]") == "schpartxxv" and law.fed_sched_key("SCHEDULE II.1") == "schii.1"


def t_off_find_exit_codes_and_flag_locations():
    r = offline(FED_XML_A4, src="ca", code="C-99", find="data mobility")
    assert r["matches"] and law.exit_code(r) == 5, r
    out = law.format_text(r)
    assert "NOT IN FORCE (amendment not in force: 2026, c. 3, s. 389)" in out and "s. (" not in out, out
    r = offline(ON_PAGE, find="imposed under section 75.1")
    assert r["matches"] and law.exit_code(r) == 5, r["matches"]
    r = offline(ON_PAGE, find="A payment is due")
    assert law.exit_code(r) == 0


def t_off_not_found_and_odd_targets():
    r = offline(FED_XML_A4, src="ca", code="C-99", targets=["12", "999"])
    assert any(f["level"] == "CHECK" and "Not found in this law" in f["text"] for f in flags_for(r, "s. 999")), r["flags"]
    assert law.exit_code(r) == 4
    r = offline(FED_XML_A4, src="ca", code="C-99", targets=["schv(1)"])
    assert any("part in brackets was ignored" in f["text"] for f in flags_for(r, "Sch. V")), r["flags"]
    for bad in ("schform", "Form", "schpart"):
        try:
            law.parse_target(bad)
            raise AssertionError(bad + " should be refused")
        except law.LawError as e:
            assert e.code == 2
    assert law.cite("sch:1") == "Schedule, 1" and law.cite("s. 43(2)") == "s. 43(2)"
    for c in ("Sch. 1", "Sch. 1, 4.1.3", "Sch. V, Part II, 2", "Form 5.1", "Sch. to Part XXV", "Sch. VI, Part VIII",
              "Schedule, 1", "s. 43(2)", "Sch. 1, 2(1)(a)"):
        assert law.cite(law.parse_target(c)[0] + ("(" + law.parse_target(c)[1] + ")" if law.parse_target(c)[1] else "")) \
            .startswith(c.split("(")[0]), c
    try:
        law.parse_target("43,44")
        raise AssertionError("43,44 should be refused")
    except law.LawError:
        pass
    try:
        offline(ON_PAGE, define="x" * 600)
        raise AssertionError("an over-long term should be refused")
    except law.LawError as e:
        assert e.code == 2


def t_off_note_number_boundary_and_time_budget():
    txt = "Note: On a day to be named, subsection 1 (2) of the Act is amended and section 10 of the Act is repealed."
    assert law.note_skips_subsection(txt, "1", "3") is True, "a note on s. 10 is not a note on all of s. 1"
    saved = law._BUDGET["until"]
    law._BUDGET["until"] = time.time() - 1
    t0 = time.time()
    try:
        law.fetch("https://www.ontario.ca/laws/statute/zz-budget-test", fresh=True)
        raise AssertionError("fetch should stop when the lookup's time budget is used up")
    except law.LawError as e:
        assert e.code == 3 and "time limit" in str(e) and time.time() - t0 < 5, e
    finally:
        law._BUDGET["until"] = saved


def t_live_fbcsa_section_70_is_not_69():
    r = run("on", "02f33", ["70", "69", "95"])
    assert r["sections"][0]["found"] and r["sections"][0]["headnote"] == "Search warrant", r["sections"][0]
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 69")), r["flags"]
    assert "96 (1)" not in text_of(r, 2), text_of(r, 2)[-300:]


def t_live_fed_range_and_schedule_fixes():
    r = run("ca", "C-46", ["595", "schpartxxv"])
    assert any(f["level"] == "REPEALED" for f in flags_for(r, "s. 595")), r["flags"]
    assert r["sections"][1]["found"] and not any("Two schedules" in f["text"] for f in r["flags"])
    r = run("ca", "E-15", define="health care facility")
    assert r["definitions"] and r["definitions"][0]["label"].startswith("Sch. V, Part II, 1"), r["definitions"]
    r = run("ca", "E-15", find="In this Part,")
    assert len(r["matches"]) >= 8 and "schv:ii:1" in [h["section"] for h in r["matches"]], r["matches"]


FED_XML_SECNOTE = """<?xml version="1.0" encoding="UTF-8"?>
<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:pit-date="2026-10-29" lims:current-date="2026-10-29" in-force="yes">
<Identification><InstrumentNumber>SOR/2099-1</InstrumentNumber><ShortTitle>Note Regulations</ShortTitle></Identification>
<Body>
<Section><MarginalNote>Start</MarginalNote><Label><FootnoteRef idref="s8">*</FootnoteRef>8</Label>
<Subsection><Label>(1)</Label><Text>These Regulations come into force on a day.</Text></Subsection>
<Subsection><MarginalNote>Section 6</MarginalNote><Label>(2)</Label><Text>Section 6 comes into force later.</Text></Subsection>
<Footnote id="s8" placement="section" status="editorial"><Label>*</Label><Text>[Note: Regulations in force July 1, 2014.]</Text></Footnote>
<HistoricalNote><HistoricalNoteSubItem>SOR/2099-2, s. 1</HistoricalNoteSubItem></HistoricalNote></Section>
<Section><Label><FootnoteRef idref="s29">*</FootnoteRef>29</Label>
<Subsection><Label>(1)</Label><Text>The Part shall be reviewed.</Text></Subsection>
<Subsection><Label>(2)</Label><Text>The committee shall report.<Footnote id="s29" placement="section" status="editorial"><Label>*</Label><Text>[Note: Part 1 in force January 1, 2001.]</Text></Footnote> Then it ends.</Text></Subsection></Section>
<Section><Label>30</Label>
<Subsection><Label>(1)</Label><Text>The Part does not apply.</Text></Subsection>
<Subsection><Label><FootnoteRef idref="s30">*</FootnoteRef>(2)</Label><Text>Subsection (1) ceases to have effect.<FootnoteRef idref="s30">*</FootnoteRef></Text>
<Footnote id="s30" placement="section" status="editorial"><Label>*</Label><Text>[Note: Section 30 in force January 1, 2001.]</Text></Footnote></Subsection></Section>
<Section><Label><FootnoteRef idref="a">*</FootnoteRef><FootnoteRef idref="b">**</FootnoteRef>31</Label>
<Subsection><Label>(1)</Label><Text>First rule.</Text></Subsection>
<Subsection><Label>(2)</Label><Text>The committee shall report.<Footnote id="a"><Label>*</Label><Text>[Note: A.]</Text></Footnote><Footnote id="b"><Label>**</Label><Text>[Note: B.]</Text></Footnote> Then it ends.</Text></Subsection></Section>
</Body>
</Regulation>"""


def t_off_fed_section_footnote_goes_with_each_subsection():
    r = offline(FED_XML_SECNOTE, src="ca", code="SOR/2099-1", targets=["8(1)", "8(2)", "8", "8(3)", "29(1)", "29(2)", "30(1)", "30(2)"])
    note8 = "* [Note: Regulations in force July 1, 2014.]"
    for i in range(3):
        assert text_of(r, i).count(note8) == 1, (i, text_of(r, i))
    assert text_of(r, 0).startswith("8 (1) These") and "Section 6 comes" not in text_of(r, 0), text_of(r, 0)
    assert r["sections"][0]["headnote"] == "Start" and r["sections"][0]["history"] == ["History: SOR/2099-2, s. 1"], r["sections"][0]
    assert any("Subsection (3) was not found" in f["text"] for f in flags_for(r, "s. 8(3)")), r["flags"]
    assert "[Note: Part 1 in force" in text_of(r, 4) and "[Note: Part 1 in force" in text_of(r, 5), (text_of(r, 4), text_of(r, 5))
    assert "The committee shall report. Then it ends." in text_of(r, 5), text_of(r, 5)
    assert "[Note: Section 30" not in text_of(r, 6) and "[Note: Section 30" in text_of(r, 7), (text_of(r, 6), text_of(r, 7))
    assert law.exit_code(r) == 0 and [f["where"] for f in r["flags"]] == ["s. 8(3)"], r["flags"]
    r = offline(FED_XML_SECNOTE, src="ca", code="SOR/2099-1", targets=["31(1)", "31(2)"])
    for i in range(2):                  # two footnotes side by side: both kept, and no text after them lost
        assert "[Note: A.]" in text_of(r, i) and "[Note: B.]" in text_of(r, i), text_of(r, i)
    assert "The committee shall report. Then it ends." in text_of(r, 1), text_of(r, 1)


FED_XML_ORDER = """<?xml version="1.0" encoding="utf-8"?><Regulation lims:current-date="2019-06-21" regulation-type="SI" xmlns:lims="http://justice.gc.ca/lims">
<Identification><InstrumentNumber>SI/2099-1</InstrumentNumber><LongTitle>Test Reproduction Order</LongTitle></Identification>
<Order><Provision><Text>Whereas the law should be widely known;</Text></Provision>
<Provision><Heading level="1"><TitleText>Test Reproduction Order</TitleText></Heading></Provision>
<Provision><Text>Anyone may reproduce the test enactments without charge.</Text></Provision>
<Provision in-force="no"><Text>A later rule that is not in force.</Text></Provision></Order>
</Regulation>"""


def t_off_fed_unsectioned_order():
    r = offline(FED_XML_ORDER, src="ca", code="SI/2099-1", targets=["order"])
    t = text_of(r)
    assert "Anyone may reproduce the test enactments" in t and "Whereas the law" in t, t
    assert "Test Reproduction Order" not in t, t           # the heading is the title, not order text
    assert "[NOT YET IN FORCE]" in t or any("not yet in force" in f["text"].lower() for f in r["flags"]), (t, r["flags"])
    assert law.format_text(r).count("== Order ==") == 1, law.format_text(r)
    r = offline(FED_XML_ORDER, src="ca", code="SI/2099-1", find="reproduce the test")
    assert law.exit_code(r) == 0 and "Order: ..." in law.format_text(r), law.format_text(r)


def t_off_targets_order_and_preamble():
    assert law.parse_target("order") == ("order", None) and law.parse_target("Preamble") == ("preamble", None)
    assert law.cite("order") == "Order" and law.cite("preamble") == "Preamble"
    assert law.parse_target("s. 43(2)") == ("43", "2")


def t_off_justice_currency_line():
    page = "<html><body><p>Act current to 2026-10-29 and last amended on 2026-01-01</p></body></html>"
    assert law.justice_currency_line(page) == "Act current to 2026-10-29 and last amended on 2026-01-01"
    page = "<html><body><p>Regulations are current to 2026-10-29</p><p>Notes : See coming into force</p></body></html>"
    assert law.justice_currency_line(page) == "Regulations current to 2026-10-29", law.justice_currency_line(page)
    assert law.justice_currency_line("<html><body>nothing here</body></html>") is None


def t_off_xml_with_entities_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><Regulation>&b;</Regulation>'
    try:
        law.read_xml(bomb)
        raise AssertionError("XML with entities was read")
    except law.LawError as e:
        assert e.code == 3, e.code
    assert law.read_xml("<Regulation><Body/></Regulation>").tag == "Regulation"


def t_off_cache_folder_must_be_private():
    if os.name == "nt":
        return
    import contextlib
    import io
    import tempfile
    saved = law.CACHE_DIR
    with tempfile.TemporaryDirectory() as d:
        try:
            shared = os.path.join(d, "shared")
            os.mkdir(shared)
            os.chmod(shared, 0o777)
            law.CACHE_DIR = shared
            with contextlib.redirect_stderr(io.StringIO()) as err:
                assert law._cache_ready() is False
            assert "not using the cache folder" in err.getvalue(), err.getvalue()
            assert law.cache_get("https://example.invalid/x", "html") is None
            assert law._write_atomic(os.path.join(shared, "x"), b"data") is False and not os.listdir(shared)
            law.CACHE_DIR = os.path.join(d, "private")
            assert law._cache_ready() is True
            assert os.stat(law.CACHE_DIR).st_mode & 0o077 == 0, oct(os.stat(law.CACHE_DIR).st_mode)
        finally:
            law.CACHE_DIR = saved


def t_off_unknown_option_is_bad_usage():
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert law.main(["on", "02c30", "43", "--frsh"]) == 2
    assert "Unknown option --frsh" in err.getvalue(), err.getvalue()


def t_off_crown_notice():
    on = {"source": "on", "consolidation": "Consolidation Period: From July 1, 2025 to the e-Laws currency date.",
          "title": "Human Rights Code, R.S.O. 1990, c. H.19"}
    assert law.crown_notice(on) == "Ontario law text © King's Printer for Ontario, 2025.", law.crown_notice(on)
    assert law.crown_notice(dict(on, consolidation=None)).endswith("Ontario, 1990.")
    assert law.crown_notice({"source": "on", "title": "O. Reg. 17/05: GENERAL"}).endswith("Ontario, 2005.")
    assert law.crown_notice({"source": "on", "title": "Something"}) == "Ontario law text © King's Printer for Ontario."
    assert law.crown_notice({"source": "ca", "title": "Criminal Code"}) is None
    r = offline(ON_PAGE, targets=["69"])
    assert law.format_text(r).splitlines()[-1].startswith("Ontario law text © King's Printer for Ontario"), law.format_text(r)


def t_off_mcp_cap_keeps_warnings_and_notice():
    import mcp_server
    body = "\n".join("x" * 80 for _ in range(5000))
    tail = "\nCHECK BEFORE RELYING ON THIS:\n- CHECK (s. 1): a warning\n" + law.FOOTER + "\nOntario law text © King's Printer for Ontario, 2025."
    out = mcp_server.cap("SOURCE: x\n" + body + tail, limit=20000)
    assert len(out) <= 20000 and out.endswith(tail) and "[Output cut" in out, (len(out), out[-300:])
    out = mcp_server.cap("SOURCE: x\n" + body + "\n" + law.FOOTER, limit=20000)
    assert len(out) <= 20000 and out.endswith("\n" + law.FOOTER), out[-200:]


OFFLINE = "--offline" in sys.argv   # only the tests that need no network (CI uses this)
TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t_") and (not OFFLINE or "off" in k)]

if __name__ == "__main__":
    for fn in TESTS:
        check(fn.__name__, fn)
    bad = [r for r in results if not r[1]]
    for name, ok, msg in results:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {msg}"))
    print(f"\n{len(results) - len(bad)} of {len(results)} passed")
    sys.exit(1 if bad else 0)
