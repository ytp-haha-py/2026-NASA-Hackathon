"""Nonexclusive research categories. Mappings are exact, reviewed source unit mappings."""

from analog_pipelines.geology.models import Lithology, Process

CANDIDATES = {
    "igneous": (
        "basalt",
        "anorthosite",
        "olivine_rich_igneous_rock",
        "olivine",
        "pyroxene",
        "plagioclase",
        "ilmenite",
    ),
    "impact": (
        "impact_breccia",
        "impact_melt_rock",
        "shock_metamorphosed_minerals",
        "impact_ejecta",
    ),
    "sedimentary": (
        "sandstone",
        "mudstone",
        "conglomerate",
        "sedimentary_deposits",
        "fluvial_deposits",
        "lacustrine_deposits",
    ),
    "aqueous_alteration": (
        "clay_minerals",
        "hydrated_sulfates",
        "carbonates",
        "iron_oxides_with_supporting_geological_evidence",
    ),
}

# Broad GLiM volcanic units remain broad volcanic units, never basalt.
GLIM_ROCKS = {
    "vb": ["basic_volcanic_rocks"],
    "vi": ["intermediate_volcanic_rocks"],
    "va": ["acid_volcanic_rocks"],
    "vu": ["volcanic_rocks_undifferentiated"],
    "pb": ["basic_plutonic_rocks"],
    "pi": ["intermediate_plutonic_rocks"],
    "pa": ["acid_plutonic_rocks"],
    "pu": ["plutonic_rocks_undifferentiated"],
    "ss": ["siliciclastic_sedimentary_rocks"],
    "sc": ["carbonate_sedimentary_rocks"],
    "sm": ["mixed_sedimentary_rocks"],
    "su": ["unconsolidated_sediments"],
    "ev": ["evaporites"],
    "mt": ["metamorphic_rocks"],
    "wb": ["water"],
    "ig": ["ice_glaciers"],
    "nd": ["no_data"],
}


def glim_mapping(code: str) -> tuple[list[str], str | None]:
    rocks = GLIM_ROCKS.get(code.lower(), [])
    category = None
    if code.lower() in {"vb", "vi", "va", "vu", "pb", "pi", "pa", "pu"}:
        category = "igneous"
    elif code.lower() in {"ss", "sc", "sm", "su", "ev"}:
        category = "sedimentary"
    return rocks, category


def process_from_unit(unit: Lithology, category: str, statement: str) -> Process:
    return Process(
        category=category,
        process=category,
        evidence_ids=[unit.evidence_id],
        supporting_statement=statement,
        basis="mapped_geology",
        confidence="mapped",
    )
