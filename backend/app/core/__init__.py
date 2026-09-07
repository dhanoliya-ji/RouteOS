"""Cross-cutting plumbing: config, logging, errors, security, redis, metrics.

The bottom layer — every other package may import it, and it imports none of
them, so it can never create a cycle. See README.md.
"""
