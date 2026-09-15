"""The complete write vocabulary. Four operations. That is the whole API.

Deliberately absent:
  * render / compose / regenerate -- no operation rewrites a document.
  * set_contact / add_contact / remove_contact -- a contact IS a row in the
    call-sheet table. Collapsing them removes two long-standing bugs at once:
    "set_contact has no email field" and "set_contact cannot rename", because
    update_row can set any cell of any row.

Every content-producing op carries a `source`. This is not documentation --
it is a required field, so an unsourced fact cannot be represented. Larissa,
22 Aug 2026: "als iets niet expliciet in de emails, PDF of ClickUp staat, zet
ik het als vraag onderaan het draaiboek, niet als feit erin."
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


class Category(str, Enum):
    TECHNIEK = "techniek"        # light red
    HOSPITALITY = "hospitality"  # light green  (catering is an alias)
    INRICHTING = "inrichting"    # light blue
    ALGEMEEN = "algemeen"        # white

    @classmethod
    def _missing_(cls, value):
        # `Category.CATERING` used to blow up mid-render with AttributeError.
        # Accept the synonyms people and LLMs actually reach for.
        alias = {
            "catering": cls.HOSPITALITY,
            "hospitality/catering": cls.HOSPITALITY,
            "horeca": cls.HOSPITALITY,
            "technique": cls.TECHNIEK,
            "tech": cls.TECHNIEK,
            "setup": cls.INRICHTING,
            "general": cls.ALGEMEEN,
            "white": cls.ALGEMEEN,
            "": cls.ALGEMEEN,
        }
        if isinstance(value, str):
            return alias.get(value.strip().lower())
        return None


class SourceKind(str, Enum):
    EMAIL = "email"          # a Gmail thread -- ref = message id or subject+date
    CLICKUP = "clickup"      # task field   -- ref = task id + field
    XERO = "xero"            # quote line   -- ref = quote number (verify vs task!)
    LARISSA = "larissa"      # she said it directly -- ref = date/message
    HOUSE_RULE = "house_rule"  # a standing rule in rules/house_rules.md
    DOC = "doc"              # already in the document; a correction of it


class Source(BaseModel):
    """Where this content came from. Recorded in the ledger, never in the doc
    (visible source citations are forbidden -- sources are internal validation)."""

    kind: SourceKind
    ref: str = Field(min_length=1, description="Message id, task id, quote number, or date Larissa said it")
    quote: str = Field(
        min_length=3,
        description="The literal snippet from that source supporting this content. "
                    "If you cannot quote it, you do not know it -- raise an open question instead.",
    )


class AddRow(BaseModel):
    op: Literal["add_row"] = "add_row"
    section: str = Field(description="Section name, e.g. 'Tijdschema'. Must already exist in the doc.")
    values: list[str] = Field(description="Cell text, left to right, matching the table's columns.")
    after_row_id: str | None = Field(
        default=None,
        description="Insert after this row. Defaults to the end of the section. "
                    "Must be a data row -- anchoring on a section band or header row is refused.",
    )
    category: Category = Category.ALGEMEEN
    source: Source = Field(description="Where this content comes from. If you cannot quote a source for it, you do not know it -- put it in Open Punten as a question instead of writing it as fact.")
    reason: str = Field(
        default="",
        description="One plain sentence for Larissa: why this change. Shown next to it "
                    "when she reviews. Required when proposing.",
    )

    @field_validator("values")
    @classmethod
    def _not_all_blank(cls, v: list[str]) -> list[str]:
        if not any(x.strip() for x in v):
            raise ValueError("refusing to add a row with no content")
        return v


class UpdateRow(BaseModel):
    op: Literal["update_row"] = "update_row"
    row_id: str = Field(description="Structural id from read(), e.g. 't0r14'.")
    expect_contains: str = Field(
        description="Text that must currently appear somewhere in this row. Proves you "
                    "are editing the row you think you are. Empty string: the row must "
                    "be completely blank (filling in an empty row).",
    )
    set_values: dict[str, str] = Field(
        description="Column -> new text. Key by column index ('0','1') or header name ('Tijd', 'Activiteit').",
    )
    category: Category | None = None
    source: Source = Field(description="Where this content comes from. If you cannot quote a source for it, you do not know it -- put it in Open Punten as a question instead of writing it as fact.")
    reason: str = Field(
        default="",
        description="One plain sentence for Larissa: why this change. Shown next to it "
                    "when she reviews. Required when proposing.",
    )


class RemoveRow(BaseModel):
    op: Literal["remove_row"] = "remove_row"
    row_id: str
    expect_contains: str = Field(
        description="Text that must currently appear in this row. Empty string: the row "
                    "must be completely blank.")
    reason: str = Field(min_length=3, description="Why this row goes. Shown to Larissa and recorded in the ledger.")


class ReplaceText(BaseModel):
    """Document-wide literal replacement.

    Exists for one rule: "Ga door het hele draaiboek en pas het gastenaantal aan!
    Niet alleen 1x maar consequent." Per-row edits cannot satisfy a
    consistency rule; this can.
    """

    op: Literal["replace_text"] = "replace_text"
    find: str = Field(min_length=2)
    replace: str
    match_case: bool = True
    source: Source = Field(description="Where this content comes from. If you cannot quote a source for it, you do not know it -- put it in Open Punten as a question instead of writing it as fact.")
    reason: str = Field(
        default="",
        description="One plain sentence for Larissa: why this change. Shown next to it "
                    "when she reviews. Required when proposing.",
    )


Op = Annotated[
    Union[AddRow, UpdateRow, RemoveRow, ReplaceText],
    Field(discriminator="op"),
]


class EditRequest(BaseModel):
    doc_id: str
    expected_revision: str = Field(
        description="revision_id from your most recent read() of this doc. If Larissa has "
                    "edited since, the write is refused and you must re-read. Her edits win.",
    )
    edits: list[Op] = Field(min_length=1)
    note: str = Field(default="", description="One line: what this batch is for. Goes in the ledger.")
