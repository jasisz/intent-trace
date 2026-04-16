# Change request: reject items with negative prices

Currently, items with a negative `price` are treated as refunds — they subtract from the subtotal during order calculation.

We want to **explicitly reject them as invalid input** when computing the total. The primary total-calculation entry point should surface a validation error when any item has a negative price, instead of silently treating it as a refund.

Keep the existing handling of `quantity`: zero and negative quantities currently model removed line items, and that behavior should remain unchanged.
