"""Python translation of the Aver payment operations showcase.

This variant replaces the one-event-at-a-time ingestion flow with an atomic
batch: a burst of raw webhooks is either fully normalized, appended, and
replayed, or the pre-batch state is preserved and the operator gets a pointer
at the offending event. Single-webhook ingestion is expressed as a one-element
batch so there is only one code path.
"""
