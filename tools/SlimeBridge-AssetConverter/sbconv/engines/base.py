"""Extraction-engine contract. Engines are interchangeable back ends (UnityPy today; AssetRipper /
AssetsTools.NET adapters can implement the same interface) so a type or Unity version that one tool
cannot read can be delegated to another without touching the converter."""

from dataclasses import dataclass, field


@dataclass
class EngineCapabilities:
    name: str
    version: str
    license: str
    unity_versions: str
    object_types: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


class ExtractionEngine:
    name = 'abstract'

    def capabilities(self) -> EngineCapabilities:
        raise NotImplementedError

    def open(self, install: dict):
        """Return a Session bound to one analysed install (see detect.install.analyse)."""
        raise NotImplementedError


class EngineError(RuntimeError):
    pass
