"""A features/ package mid-migration: two migrated features and one still on the
old router + manifest shape, which is what the loader sees in phases B to E.

`alpha` sits later in the chain than `zulu` on purpose, so a test can tell the
alphabetical discovery order apart from the resolved `at` order.
"""
