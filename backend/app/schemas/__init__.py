"""Pydantic request/response models — the API's public contract.

Separate from app/models/ so storage can change without breaking clients, and
so nothing is exposed or trusted by accident. See README.md.
"""
