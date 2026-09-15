"""Draaiboek: surgical Google Docs editing for Leeuwenbergh event runbooks.

Design invariants (these are the product, not implementation details):
  1. The Google Doc is the only source of truth. There is no model, no
     synthesiser, no renderer, no baseline file.
  2. There is no code path that rewrites a document wholesale. Destruction
     of Larissa's manual edits is not guarded against -- it is unrepresentable.
  3. Every write is locked to the revision that was read. She edits between
     read and write -> the write is refused.
  4. Every fact written carries provenance. Unsourced content cannot be
     expressed in the op schema.
"""

__version__ = "0.1.0"
