"""Real (network-backed) integration clients, always behind config flags.

Mocks remain the default everywhere; nothing in this package is imported unless its
flag is enabled, and every client's HTTP goes through an injectable ``httpx`` client so
request/response mapping is unit-tested offline with ``httpx.MockTransport``.
"""
