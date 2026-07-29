"""Typed access to the vendored certified ontology (``ontology/ontology.yml``).

``ontology.yml`` is machine-proposed, human-certified data — presence in the
file **is** certification (only ``planned``/``retired`` entities are stamped
explicitly). This package parses it into the pydantic models below via
``get_ontology()`` (cached) or ``load()`` (explicit path, uncached).
"""

from .loader import get_ontology, load
from .models import Column, Entity, Metric, NegativeConstraint, Ontology

__all__ = [
    "Column",
    "Entity",
    "Metric",
    "NegativeConstraint",
    "Ontology",
    "get_ontology",
    "load",
]
