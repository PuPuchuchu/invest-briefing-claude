"""
Concrete price-data providers and reference loaders for src/price.

Unlike src/price (pure, no network, no IO), this package may perform
network requests (provider HTTP) and read reference files. It still
never writes price data anywhere and never logs payloads, URLs with
credentials, or headers.
"""
