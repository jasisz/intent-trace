"""Python translation of the Aver payment operations showcase.

This variant rebuilds every payment state strictly from its event log using a
single pure entry point (``rebuildPaymentState``). All other ledger helpers
compose on top of that rebuilder so no independent field-updating path can
drift out of sync with the canonical replay function.
"""
