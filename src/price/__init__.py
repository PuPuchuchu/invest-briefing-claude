"""
Common price calculation engine (P3 v7 / engine design v1, 2026-11-27).

Status: IMPLEMENTED -- SYNTHETIC DATA ONLY / NOT OPERATIONALLY APPROVED.

This package contains no HTTP client, no API-key handling, no network
access and no persistence. Every provider in this round is synthetic
(`InMemoryPriceProvider`). A real provider (e.g. Massive) may only be
added after a separately approved PoC.

Capture class of every result produced here: EPHEMERAL_ONLY -- neither
raw prices nor price-derived results are written anywhere by this
package.
"""
