from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)


class FormatType(StrEnum):
    SFUSO = "sfuso"
    CONFEZIONE_SINGOLA = "confezione_singola"
    MULTIPACK_OMOGENEO = "multipack_omogeneo"
    N_PEZZI_PESO_TOTALE = "n_pezzi_peso_totale"
    MULTIPACK_PEZZI = "multipack_pezzi"
    N_LAVAGGI = "n_lavaggi"
    MULTIPACK_ETEROGENEO = "multipack_eterogeneo"
    PESO_RANGE = "peso_range"
    ROTOLI = "rotoli"


class FormatUnit(StrEnum):
    G = "g"
    KG = "kg"
    ML = "ml"
    L = "L"
    CL = "cl"
    ETTO = "etto"


class QuantityUnit(StrEnum):
    G = "g"
    KG = "kg"
    L = "L"


class BulkUnit(StrEnum):
    KG = "kg"
    ETTO = "etto"
    LITRO = "litro"
    PEZZO = "pezzo"


_FORMAT_UNIT_ALIASES = {
    "g": FormatUnit.G,
    "gr": FormatUnit.G,
    "grammi": FormatUnit.G,
    "kg": FormatUnit.KG,
    "kilo": FormatUnit.KG,
    "kilogrammo": FormatUnit.KG,
    "ml": FormatUnit.ML,
    "cl": FormatUnit.CL,
    "l": FormatUnit.L,
    "lt": FormatUnit.L,
    "ltr": FormatUnit.L,
    "litro": FormatUnit.L,
    "litri": FormatUnit.L,
    "etto": FormatUnit.ETTO,
}

_QUANTITY_UNIT_ALIASES = {
    "g": QuantityUnit.G,
    "gr": QuantityUnit.G,
    "grammi": QuantityUnit.G,
    "kg": QuantityUnit.KG,
    "kilo": QuantityUnit.KG,
    "kilogrammo": QuantityUnit.KG,
    "l": QuantityUnit.L,
    "lt": QuantityUnit.L,
    "ltr": QuantityUnit.L,
    "litro": QuantityUnit.L,
    "litri": QuantityUnit.L,
}

_BULK_UNIT_ALIASES = {
    "kg": BulkUnit.KG,
    "kilo": BulkUnit.KG,
    "kilogrammo": BulkUnit.KG,
    "etto": BulkUnit.ETTO,
    "litro": BulkUnit.LITRO,
    "litri": BulkUnit.LITRO,
    "l": BulkUnit.LITRO,
    "pezzo": BulkUnit.PEZZO,
    "pz": BulkUnit.PEZZO,
}

_EMPTY_FORMAT: dict[str, Any] = {
    "tipo": None,
    "quantita": None,
    "peso_volume": None,
    "unita_misura": None,
    "peso_approssimativo": False,
    "peso_sgocciolato": None,
    "unita_misura_sgocciolato": None,
    "num_pezzi": None,
    "peso_volume_totale": None,
    "num_lavaggi": None,
    "quantita_totale": None,
    "unita_misura_quantita": None,
    "peso_volume_min": None,
    "peso_volume_max": None,
    "num_rotoli": None,
    "num_veli": None,
    "num_strappi_per_rotolo": None,
    "num_fogli_totali": None,
    "quantita_omaggio": None,
    "unita_sfuso": None,
    "componenti": None,
    "varianti": None,
}


@dataclass(frozen=True)
class NormalizedFormatBundle:
    format_normalized: dict[str, Any]
    format_compact: dict[str, Any]
    format_key: str
    format_label: str


def empty_product_format() -> dict[str, Any]:
    return dict(_EMPTY_FORMAT)


def _normalize_scalar(value: Any) -> Any:
    if value == "":
        return None
    return value


def _normalize_enum(value: Any, aliases: dict[str, StrEnum]) -> StrEnum | None:
    value = _normalize_scalar(value)
    if value is None:
        return None
    if isinstance(value, StrEnum):
        return value
    cleaned = str(value).strip().lower()
    return aliases.get(cleaned)


class ProductFormatComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str
    quantita: int | None = None
    peso_volume: float | None = None
    unita_misura: FormatUnit | None = None

    @field_validator("unita_misura", mode="before")
    @classmethod
    def _normalize_unit(cls, value: Any) -> FormatUnit | None:
        return _normalize_enum(value, _FORMAT_UNIT_ALIASES)


class ProductFormatVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome_variante: str
    formato: "ProductFormat"


class ProductFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: FormatType | None = None
    quantita: int | None = None
    peso_volume: float | None = None
    unita_misura: FormatUnit | None = None
    peso_approssimativo: bool = False
    peso_sgocciolato: float | None = None
    unita_misura_sgocciolato: FormatUnit | None = None
    num_pezzi: int | None = None
    peso_volume_totale: float | None = None
    num_lavaggi: int | None = None
    quantita_totale: float | None = None
    unita_misura_quantita: QuantityUnit | None = None
    peso_volume_min: float | None = None
    peso_volume_max: float | None = None
    num_rotoli: int | None = None
    num_veli: int | None = None
    num_strappi_per_rotolo: int | None = None
    num_fogli_totali: int | None = None
    quantita_omaggio: int | None = None
    unita_sfuso: BulkUnit | None = None
    componenti: list[ProductFormatComponent] | None = None
    varianti: list[ProductFormatVariant] | None = None

    @field_validator("unita_misura", "unita_misura_sgocciolato", mode="before")
    @classmethod
    def _normalize_format_units(cls, value: Any) -> FormatUnit | None:
        return _normalize_enum(value, _FORMAT_UNIT_ALIASES)

    @field_validator("unita_misura_quantita", mode="before")
    @classmethod
    def _normalize_quantity_unit(cls, value: Any) -> QuantityUnit | None:
        return _normalize_enum(value, _QUANTITY_UNIT_ALIASES)

    @field_validator("unita_sfuso", mode="before")
    @classmethod
    def _normalize_bulk_unit(cls, value: Any) -> BulkUnit | None:
        return _normalize_enum(value, _BULK_UNIT_ALIASES)

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "ProductFormat":
        required_by_type: dict[FormatType, tuple[str, ...]] = {
            FormatType.SFUSO: ("unita_sfuso",),
            FormatType.CONFEZIONE_SINGOLA: ("peso_volume", "unita_misura"),
            FormatType.MULTIPACK_OMOGENEO: ("quantita", "peso_volume", "unita_misura"),
            FormatType.N_PEZZI_PESO_TOTALE: ("num_pezzi", "peso_volume_totale", "unita_misura"),
            FormatType.MULTIPACK_PEZZI: ("quantita", "num_pezzi", "peso_volume_totale", "unita_misura"),
            FormatType.N_LAVAGGI: ("num_lavaggi", "quantita_totale", "unita_misura_quantita"),
            FormatType.MULTIPACK_ETEROGENEO: ("componenti",),
            FormatType.PESO_RANGE: ("peso_volume_min", "peso_volume_max", "unita_misura"),
            FormatType.ROTOLI: tuple(),
        }
        if self.tipo is None:
            return self
        missing = [
            field_name
            for field_name in required_by_type[self.tipo]
            if getattr(self, field_name) in (None, [], "")
        ]
        if missing:
            raise ValueError(f"Missing required format fields for {self.tipo}: {', '.join(missing)}")
        return self


ProductFormatVariant.model_rebuild()


def _compact_format(value: Any) -> Any:
    if isinstance(value, list):
        compact_items = [_compact_format(item) for item in value]
        return [item for item in compact_items if item not in (None, {}, [], "")]
    if isinstance(value, dict):
        compact_dict = {
            key: _compact_format(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
        if compact_dict.get("peso_approssimativo") is False:
            compact_dict.pop("peso_approssimativo", None)
        return compact_dict
    return value


def _format_key_from_compact(compact: dict[str, Any]) -> str:
    return f"v1:{json.dumps(compact, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"


def _number_to_label(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{float(value):g}".replace(".", ",")


def _unit_label(value: str | None) -> str:
    return value or ""


def _format_label_from_normalized(data: dict[str, Any]) -> str:
    tipo = data.get("tipo")
    if not tipo:
        return ""
    if tipo == FormatType.SFUSO:
        bulk = data.get("unita_sfuso")
        mapping = {
            BulkUnit.KG: "al kg",
            BulkUnit.ETTO: "all'etto",
            BulkUnit.LITRO: "al litro",
            BulkUnit.PEZZO: "al pezzo",
        }
        return mapping.get(bulk, "")
    if tipo == FormatType.CONFEZIONE_SINGOLA:
        label = f"{_number_to_label(data.get('peso_volume'))} {_unit_label(data.get('unita_misura'))}".strip()
        if data.get("peso_approssimativo"):
            label = f"ca. {label}"
        if data.get("peso_sgocciolato") is not None:
            label = (
                f"{label} ({_number_to_label(data.get('peso_sgocciolato'))} "
                f"{_unit_label(data.get('unita_misura_sgocciolato'))} sgocc.)"
            )
        return label
    if tipo == FormatType.MULTIPACK_OMOGENEO:
        label = (
            f"{_number_to_label(data.get('quantita'))}x"
            f"{_number_to_label(data.get('peso_volume'))} {_unit_label(data.get('unita_misura'))}"
        )
        if data.get("quantita_omaggio") is not None:
            label = f"{label} + {_number_to_label(data.get('quantita_omaggio'))} omaggio"
        return label.strip()
    if tipo == FormatType.N_PEZZI_PESO_TOTALE:
        return (
            f"{_number_to_label(data.get('num_pezzi'))} pezzi - "
            f"{_number_to_label(data.get('peso_volume_totale'))} {_unit_label(data.get('unita_misura'))}"
        ).strip()
    if tipo == FormatType.MULTIPACK_PEZZI:
        return (
            f"{_number_to_label(data.get('quantita'))} confezioni - "
            f"{_number_to_label(data.get('num_pezzi'))} pezzi - "
            f"{_number_to_label(data.get('peso_volume_totale'))} {_unit_label(data.get('unita_misura'))}"
        ).strip()
    if tipo == FormatType.N_LAVAGGI:
        return (
            f"{_number_to_label(data.get('num_lavaggi'))} lavaggi - "
            f"{_number_to_label(data.get('quantita_totale'))} {_unit_label(data.get('unita_misura_quantita'))}"
        ).strip()
    if tipo == FormatType.MULTIPACK_ETEROGENEO:
        components = data.get("componenti") or []
        labels = []
        for component in components:
            part = component["nome"]
            if component.get("quantita") is not None:
                part = f"{component['quantita']}x {part}"
            if component.get("peso_volume") is not None and component.get("unita_misura"):
                part = f"{part} {_number_to_label(component['peso_volume'])} {component['unita_misura']}"
            labels.append(part)
        return " + ".join(labels)
    if tipo == FormatType.PESO_RANGE:
        return (
            f"{_number_to_label(data.get('peso_volume_min'))}-"
            f"{_number_to_label(data.get('peso_volume_max'))} {_unit_label(data.get('unita_misura'))}"
        ).strip()
    if tipo == FormatType.ROTOLI:
        pieces: list[str] = []
        if data.get("num_rotoli") is not None:
            pieces.append(f"{_number_to_label(data.get('num_rotoli'))} rotoli")
        if data.get("num_veli") is not None:
            pieces.append(f"{_number_to_label(data.get('num_veli'))} veli")
        if data.get("num_strappi_per_rotolo") is not None:
            pieces.append(f"{_number_to_label(data.get('num_strappi_per_rotolo'))} strappi")
        if data.get("num_fogli_totali") is not None:
            pieces.append(f"{_number_to_label(data.get('num_fogli_totali'))} fogli")
        return ", ".join(pieces)
    return ""


def build_format_bundle(payload: Any) -> NormalizedFormatBundle:
    if isinstance(payload, NormalizedFormatBundle):
        return payload
    if isinstance(payload, str):
        raise ValueError("Plain text product format is no longer supported")
    if payload is None:
        payload = {}
    normalized = ProductFormat.model_validate(payload).model_dump(mode="json")
    merged = empty_product_format()
    merged.update(normalized)
    compact = _compact_format(merged)
    return NormalizedFormatBundle(
        format_normalized=merged,
        format_compact=compact,
        format_key=_format_key_from_compact(compact),
        format_label=_format_label_from_normalized(merged),
    )


def _partial_bundle_from_payload(payload: dict[str, Any]) -> NormalizedFormatBundle:
    normalized = empty_product_format()
    partial = {key: value for key, value in payload.items() if key in normalized}
    compact = _compact_format(partial)
    normalized.update(compact)
    return NormalizedFormatBundle(
        format_normalized=normalized,
        format_compact=compact,
        format_key=_format_key_from_compact(compact),
        format_label=_format_label_from_normalized(normalized),
    )


def build_extraction_format_bundle(payload: Any) -> NormalizedFormatBundle:
    if isinstance(payload, str):
        raise ValueError("Plain text product format is no longer supported")
    try:
        return build_format_bundle(payload)
    except ValidationError:
        if not isinstance(payload, dict):
            raise
        logger.warning("Invalid extracted product format; falling back to partial canonical format: %r", payload)
        return _partial_bundle_from_payload(payload)


def normalize_format(payload: Any) -> dict[str, Any]:
    return build_format_bundle(payload).format_normalized


def compact_format(payload: Any) -> dict[str, Any]:
    return build_format_bundle(payload).format_compact


def format_to_key(payload: Any) -> str:
    return build_format_bundle(payload).format_key


def format_to_label(payload: Any) -> str:
    return build_format_bundle(payload).format_label


def explode_format_variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    parent_bundle = build_extraction_format_bundle(product.get("format"))
    variants = parent_bundle.format_normalized.get("varianti") or []
    if not variants:
        product_copy = dict(product)
        product_copy["format"] = parent_bundle.format_compact
        product_copy["_format_bundle"] = parent_bundle
        return [product_copy]

    base_name = str(product.get("name") or "").strip()
    exploded: list[dict[str, Any]] = []
    for variant in variants:
        variant_name = str(variant.get("nome_variante") or "").strip()
        child_name = base_name
        if variant_name and variant_name.lower() not in base_name.lower():
            child_name = f"{base_name} {variant_name}".strip()
        child = dict(product)
        child["name"] = child_name
        child_bundle = build_extraction_format_bundle(variant.get("formato"))
        child["format"] = child_bundle.format_compact
        child["_format_bundle"] = child_bundle
        exploded.append(child)
    return exploded
