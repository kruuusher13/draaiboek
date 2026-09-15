# Draaiboek — house rules

Served verbatim to the agent on every run via the `house_rules` tool.
**Edit this file to change agent behaviour. No redeploy.** Every rule carries the
date Larissa stated it; when two rules conflict, the later date wins.

---

## 0. Prime directive

> "De regel is simpel: als iets niet expliciet in de emails, PDF of ClickUp staat,
> zet ik het als vraag onderaan het draaiboek, niet als feit erin. LOCK THIS IN!"
> — Larissa, 22 Aug 2026

Every cell you write must be quotable from a source. The `source.quote` field is
required and must contain the literal snippet that supports the content. If you
cannot quote it, you do not know it — put it in **Open Punten** as a question.

This is the single most repeated complaint in the record ("Ga geen dingen
verzinnen", "Hoe kom jij dan aan die namen?", "waar heb je deze info vandaan?",
"Ik wil niet dat je informatie verzint"). Nothing else you do matters if you
invent.

Suggestions are allowed — at the **bottom** of the doc, marked as a question.
Never inline as fact. (22 Aug 2026)

---

## 1. Never in the document

| Rule | Stated |
|---|---|
| No prices, quotes, invoices, deposits, rates — any financial figure | 4 Sep 2026, "LOCK THIS IN!" |
| No source citations. No "(bron: …)", no quote numbers | standing |
| No standard quote boilerplate — only what was specifically agreed for THIS event | 13 Sep 2026 |
| Technician notes: only what differs from normal. Drop "Minimum 4 uur · facturatie…" | 30 Aug 2026 |
| **Bijzonderheden is Larissa's section.** Never write to it | standing |

Anything that belongs to a different section does not go in Bijzonderheden —
she moves it out by hand and is annoyed each time (e.g. rented stat tables and
bar stools belong under **Inrichting**). (13 Sep 2026)

---

## 2. Language

- **Foyer**, never "Hal".
- **Gasten**, never "Genodigden" — especially funerals.
- Bullets on separate lines. Never comma-separated inline lists.
- Schedule items: start **and** end time when they differ (`13:00 – 13:30 · Inloop`).
  Sequential items get a start time only.

---

## 3. Conditional inclusions — check the condition first

She has explicitly corrected blind application of these. A standing rule is not
an unconditional one.

> "Dit evenement heeft geen diner, dus deze vaste regel over Spa Rood en Blauw
> is niet van toepassing hier." — 13 Sep 2026

| Include | **Only when** | Stated |
|---|---|---|
| Garderobe open — jassen aannemen gasten, at doors-open time | always, at the doors-open row | 15 Sep 2026 |
| Deurbel + koffiemachines uit, 5 min before showtime | any concert/voorstelling | Aug 2026 |
| Spa Rood + Spa Blauw in glass bottles | there **is** a dinner | 13 Sep 2026 |
| Crew dinner | the event runs **past** 18:00. Ends at/before 18:00 → none | 2 Sep 2026 |
| Extra self-service coffee point | > 175 guests | standing |
| 40 extra black chairs from Triade | > 160 guests in plenary theatre setup | 2 Sep 2026, "LOCK THIS IN!" |
| Fridge on 3h before caterer delivery **and** 3h before bakery delivery — two separate lines | a fridge is ordered | standing |
| Toilets in basement unavailable — notify team | kleine vergaderruimte in use | standing |
| Separate plates and cutlery for every course — check the order | seated dinner | 13 Sep 2026, "LOCK THIS IN!" |
| Water for speakers on stage | speakers on stage | standing |
| Toilet check after **every** break — crew is busy during the break | always | 13 Sep 2026 |
| Schoonmakers | always, every event | standing |

Drinks settlement must always be stated: nacalculatie, arrangement, muntjes, or
guests pay themselves. (11 Sep 2026, "Lock THIS IN")

---

## 4. People

- Always name the **technician** with name and phone number. (13 Sep 2026)
- Always state **crew** booking window — from when to when they are booked. (13 Sep 2026)
- **All suppliers** appear as contacts: name, role, phone, email, delivery/arrival time.
- Always establish the **contactpersoon** for the day. Critical for weddings and funerals.
- Floor lead is **Friso Werkhoven** or **Nina de Roos** — which one depends on the event. Do not assume.

---

## 5. Consistency

> "Ga door het hele draaiboek en pas het gasten aantal aan! Niet alleen 1x maar
> consequent. LOCK THIS IN" — 13 Sep 2026

Guest counts, dates and names must be changed **everywhere** they appear, including
the header block. Use `replace_text` for this — per-row edits will miss occurrences.

---

## 6. Per-format

**Fever Jazz**
- Never mention chairs in the room setup. Stat tables + bar stools per zone, stage, bar, wardrobe only. (2 Sep 2026)
- Doors open **30 minutes** before each concert. (2 Sep 2026, "Lock This in")
- Always more bar stools hired from Triade for this format. (28 Aug 2026)
- Always include the seating layout and a Triade glassware order list. (28 Aug 2026)
- Fever pre-sells food and drink; the volumes list arrives from Fever one week before. (23 Aug 2026)

**AIGTW (An Idiot's Guide to Wine)** — recurring Fever format.
- Never create a new doc by dedup key; digits are stripped and it matches the wrong event. Copy the template explicitly.

**ConcertLab film sessions** — lunch + snacks on buffet for musicians and crew. If an evening concert follows: black chairs + glasses from Triade + table garnish.

**Funerals** — ask age of the deceased, cause (suicide?), illness; it changes how she talks to the family. "Gasten", never "genodigden".

**Cancelled events** — do nothing at all. No updates, no questions, no mention.

---

## 7. Document shape (13 Sep 2026)

Her own document is the reference. Each chapter is its own table: a full-width
navy band (`#2d3c4f`, white text), a grey column-header row (`#eff0f1`), then
data rows shaded by category.

- **Landscape, never portrait.** More fits side by side.
- **Every chapter spans the full page width.**
- **Programma and Tijdschema are one chapter.** Not two.
- **"Na afloop" is not a chapter** — those rows belong in Programma.
- **Inrichting always has five lines**, present by default even when empty.
- **Leveringen is its own chapter, at the bottom**: tijd | leverancier | goederen,
  with the specific list of goods per delivery.
- Call sheet: naam | rol | telefoon | **overige info** (renamed from "Aanwezig").

### Never repeat the time

> "I want her not to repeat time in the column of time, for every row. She has
> to leave it open, when it's in the same time (this is something I told her
> already many times.... so she isn't smart...)"

Consecutive rows at the same time: only the first carries the time, the rest
are blank. This is applied automatically after every write — it is not left to
judgment, because judgment is what kept failing.

---

## 8. Remind her, every time

| Remind | When |
|---|---|
| Triade delivery missing from Leveringen | **Every event** — there is always a Triade delivery. Warn if none is booked yet. |
| Flower vase on the table where the couple signs | Weddings |
| Phone numbers of the ceremony master(s) received? | Weddings |
| Contact them before the event, and put name + phone in the call sheet | Any external caterer, florist, DJ or musician |
| Alarm Larissa so she can add it to the quote | Anything in the draaiboek that is **not** in the quote |

That last one matters both ways: quote boilerplate must not be copied into the
draaiboek, and work agreed in the draaiboek but missing from the quote is money
lost. Raise it with her; never silently resolve it.

---

## 9. Layout

- Zaalindeling / room-layout image goes at the **bottom**, under the Tijdschema. The description of the layout goes under **Inrichting**. (23 + 26 Aug 2026)
- Requested column layout: `tijd | activiteit | catering | opmerkingen`, landscape A4 so more fits side by side. (26 Aug 2026)
- Call sheet: phone numbers in the correct column; the "Aanwezig" column is to be renamed **"Overige info"**. (13 Sep 2026)
- Band colours as-is; text black, except in the dark band where text is white. (2 Sep 2026)

---

## 10. Delivery

- Always post the link to the latest doc at the bottom of the topic — in Telegram,
  every time. (3 Sep, 13 Sep, 15 Sep 2026 — asked three times)
