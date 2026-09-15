# draaiboek

**Start here**

| You are | Read |
|---|---|
| Larissa | **[HANDLEIDING.md](HANDLEIDING.md)** — de werkplek, in gewoon Nederlands |
| Florentine (the agent) | **[SETUP-FLORENTINE.md](SETUP-FLORENTINE.md)** to install, then **[AGENT.md](AGENT.md)** to work |
| Romir | the rest of this file |

---

Surgical Google Docs editing for Leeuwenbergh event runbooks.

Replaces the compose/synthesise/render pipeline. Four operations, no renderer.
Hermes proposes; Larissa reviews, answers and deploys in the workspace.

## Why this exists

The old pipeline destroyed Larissa's manual edits because it *could*:
`render()` cleared the document body and rewrote it. This one cannot, because
no operation writes more than a cell.

Four design decisions do all the work:

| | |
|---|---|
| **The doc is the database** | No model, no synthesiser, no `doc_baselines.json`. Nothing to drift out of sync. |
| **New docs are template copies** | The logo, column widths, Calibri, the legend and the band colours are right because a human made them right once, in a Google Doc. No code draws formatting, so no code can get it wrong. |
| **Writes are revision-locked** | Every write carries the `revision_id` you read. She edits in between → refused. "Larissa's edits are law" is a lock, not a sentence in a prompt. |
| **Facts carry provenance** | `source.quote` is a required field. An unsourced fact is not expressible in the schema. |

Plus: rows she deletes by hand become **tombstones** and cannot be re-added.

## Install

```bash
cd ~/romir_ws/draaiboek && python3 -m venv .venv && .venv/bin/pip install -e .
```

## Configure

```bash
export DRAAIBOEK_HOME=~/.draaiboek                 # ledger, snapshots, tombstones
export DRAAIBOEK_CLIENT_SECRET=~/.draaiboek/client_secret.json
export DRAAIBOEK_TEMPLATE_DOC_ID=<master template doc id>
export DRAAIBOEK_SANDBOX_DOC_ID=<throwaway doc id>  # doctor tests against this
export DRAAIBOEK_DRIVE_FOLDER_ID=<draaiboeken folder>
```

Use a **service account** key rather than OAuth. The expiring token is what left
Larissa stranded on 15 Sep with "ik kan de doc niet aanpassen"; service accounts
do not expire. Share the template and the draaiboeken folder with the service
account's email as Editor.

With an OAuth client secret instead: `draaiboek auth`.

```bash
draaiboek doctor      # verifies credentials, rules, template, and a live read
```

## Use

```bash
draaiboek read <doc-id-or-url>     # every row with its stable row_id
draaiboek rules                    # the house rules, as the agent sees them
draaiboek log <doc-id>             # audit trail
draaiboek protected <doc-id>       # rows she deleted; permanently blocked
draaiboek edit <doc-id> -f ops.json --dry-run
```

`ops.json`:

```json
{
  "note": "deurbel + garderobe toevoegen",
  "edits": [
    {"op": "add_row", "section": "Tijdschema",
     "values": ["19:30", "Garderobe open — jassen aannemen gasten", ""],
     "category": "algemeen",
     "source": {"kind": "larissa", "ref": "15 sep 2026",
                "quote": "bij de deuren open, de jassen aan nemen van de gasten"}}
  ]
}
```

Omit `expected_revision` on the CLI and it reads the current one first. The MCP
tool requires it explicitly — that is the point.

## The workspace (Larissa)

```bash
draaiboek ui                      # http://127.0.0.1:8765
```

1. **Events** — upcoming events from ClickUp, and at the top what Hermes is
   waiting on her for. Search takes names or dates ("26 september", "26th sept").
2. **Draaiboek** — her document in her layout. Hermes' proposed changes are in the
   sidebar, each with a keep switch, the reason, and the quote it came from. Open
   questions are answered right there. Any cell can be edited; rows added or
   removed.
3. **Sources** — everything fetched for the event: Gmail threads, Missive
   conversations with internal comments, ClickUp with comments, Xero quote lines.
   Attachments (floor plans, riders) open in place.
4. **Deploy** — every change is checked against the house rules and sources
   first; then one write, through the same revision lock as everything else.
   Answers go to Hermes (`proposal_status`, `open_point_answers`).
5. **Rules** — `rules/house_rules.md`, editable. Previous versions are kept in
   `$DRAAIBOEK_HOME/rules_history`. `guards.yaml` stays with Romir.

Rows she removes become tombstones, like rows deleted by hand in Google Docs.
Her own edits may touch Bijzonderheden; Hermes' may not.

**Access from her own laptop.** The workspace reads all mail, so it only serves
other machines with a key:

```bash
echo "DRAAIBOEK_UI_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> ~/.draaiboek/env
echo "DRAAIBOEK_PUBLIC_URL=https://<address Larissa opens>" >> ~/.draaiboek/env
draaiboek ui --host 0.0.0.0 --no-open
```

Put it behind HTTPS (Tailscale Funnel or Cloudflare Tunnel). She signs in once
with the key; the session lasts 30 days. Hermes' links use `DRAAIBOEK_PUBLIC_URL`.

## Wire into Hermes

```json
{"mcpServers": {"draaiboek": {
  "command": "~/romir_ws/draaiboek/.venv/bin/draaiboek",
  "args": ["serve"],
  "env": {"DRAAIBOEK_TEMPLATE_DOC_ID": "...", "DRAAIBOEK_DRIVE_FOLDER_ID": "..."}
}}}
```

Then give the agent `AGENT.md` as its operating procedure, and **remove its
ability to call the Docs API directly**. Hermes cannot write either: `apply_edits`
only dry-runs unless `DRAAIBOEK_DIRECT_APPLY=1`. It calls `propose_edits` and sends
Larissa the `workspace_url` on Telegram. If raw `batchUpdate` stays available,
it will be used the moment a tool refuses something — which is exactly how the
logo, the column widths and the legend were lost on 15 Sep.

## Changing the rules

`rules/house_rules.md` — judgment. Served to the agent on every run.
`rules/guards.yaml` — enforced in code. A `block` rule refuses the write.

Both are hot-reloaded. When Larissa says "lock this in", you edit a markdown
file. No redeploy, no code change, no release.

## Tests

```bash
.venv/bin/pip install -e '.[dev]' && .venv/bin/python -m pytest -q
```

`tests/fake_google.py` is an in-memory Docs that honours real index semantics:
requests are applied one at a time against freshly computed indices. A batch
ordered wrongly fails there exactly as it would on a live document. That is why
nothing needs to be tested on Larissa's docs.
