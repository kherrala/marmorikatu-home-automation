"""
Shared light/switch label tables for the marmorikatu PLC.

Both `plc_mqtt_subscriber.py` and the MCP `lights` tool import from here so
they cannot drift apart.

LIGHT_LABELS keys are bare `PersistentVars.Controls[]` indices (the same
integers used in the `marmorikatu/lights` JSON payload and in command topics
like `marmorikatu/light/<index>/set`). Values are `(finnish_name, floor)`,
where floor is 0=Kellari, 1=Alakerta, 2=Yläkerta, None=outdoor/unclassified.

SWITCH_LABELS keys are wall-switch input position numbers (1–56).

Both tables are derived from the canonical CSVs at
../marmorikatu-plc/PlcLogic/visu/buttontxt.txt and
../marmorikatu-plc/PlcLogic/visu/buttonpos.txt.
"""

LIGHT_LABELS = {
    1:  ("Kylpyhuone alakerta", 1),
    2:  ("Keittiö kaapisto ylä", 1),
    3:  ("Yläkerta aula ledi", 2),
    4:  ("Saunan laude ledi", 1),
    5:  ("Olohuone ledi", 1),
    6:  ("Kodinhoitohuone ledi", 1),
    7:  ("Keittiö kaapisto ala", 1),
    8:  ("Keittiö katto", 1),
    17: ("MH alakerta kattovalo", 1),
    18: ("MH alakerta ikkuna", 1),
    19: ("Ruokailu", 1),
    20: ("Ruokailu ikkuna", 1),
    22: ("Aatu kattovalo", 2),
    23: ("Aatu ikkunavalo", 2),
    24: ("Aula ikkunavalo", 2),
    25: ("Aula rappuset", 2),
    26: ("Yläkerta aula kattovalo", 2),
    28: ("Onni kattovalo", 2),
    29: ("Kylpyhuone yläkerta katto", 2),
    30: ("Onni ikkunavalo", 2),
    31: ("Essi vaatehuone", 2),
    32: ("Essi ikkunavalo", 2),
    33: ("Essi kattovalo", 2),
    34: ("Kylpyhuone yläkerta peilivalo", 2),
    35: ("Eteinen", 1),
    36: ("Tuulikaappi vaatehuone", 1),
    37: ("Tuulikaappi", 1),
    38: ("Sauna siivousvalo", 1),
    39: ("Tekninen tila", 1),
    40: ("Keittiö kattovalo", 1),
    41: ("Keittiö ikkunavalo", 1),
    42: ("Portaikko", 1),
    43: ("Kodinhoitohuone vaatehuone", 1),
    44: ("WC alakerta katto", 1),
    45: ("WC alakerta peili", 1),
    46: ("Olohuone ikkuna", 1),
    47: ("Sisäänkäynti", None),
    48: ("Ulkovalo terassi", None),
    49: ("Kellari etuosa", 0),
    50: ("Kellari takaosa", 0),
    51: ("Biljardipöytä", 0),
    52: ("WC kellari", 0),
    53: ("Kellari varasto", 0),
    54: ("Olohuone kattovalo", 1),
    55: ("Olohuone kattovalo 2", 1),
    56: ("Kodinhoitohuone kattovalo 2", 1),
    59: ("Autokatos", None),
    60: ("Varasto ulkovalo", None),
    61: ("Varasto", None),
}

SWITCH_LABELS = {
    1:  ("Kylpyhuone 1", 1),
    2:  ("Kylpyhuone 2", 1),
    3:  ("WC alakerta 1", 1),
    4:  ("WC alakerta 2", 1),
    5:  ("KHH 1", 1),
    6:  ("KHH 2", 1),
    7:  ("Keittiö 1", 1),
    8:  ("Keittiö 2", 1),
    9:  ("Tuulikaappi 1", 1),
    10: ("Tuulikaappi 2", 1),
    11: ("MH alakerta 1", 1),
    12: ("MH alakerta 2", 1),
    13: ("Eteinen 1", 1),
    14: ("Eteinen 2", 1),
    15: ("KHH vaatehuone", 1),
    16: ("Tuulikaappi vaatehuone", 1),
    17: ("Porras AK 1", 1),
    18: ("Porras AK 2", 1),
    19: ("Essi 1", 2),
    20: ("Essi 2", 2),
    21: ("Essi vaatehuone", 2),
    23: ("Kylpyhuone YK 1", 2),
    24: ("Kylpyhuone YK 2", 2),
    25: ("Porras YK 1", 2),
    26: ("Porras YK 2", 2),
    27: ("Aula YK 1", 2),
    28: ("Aula YK 2", 2),
    29: ("Onni 1", 2),
    30: ("Onni 2", 2),
    31: ("Aatu 1", 2),
    32: ("Aatu 2", 2),
    33: ("Tekninen tila", 1),
    34: ("Kellari WC", 0),
    35: ("Kellari eteinen 1", 0),
    36: ("Kellari eteinen 2", 0),
    37: ("Kellari 1", 0),
    38: ("Kellari 2", 0),
    41: ("Saareke 1", 1),
    42: ("Saareke 2", 1),
    43: ("Saareke 3", 1),
    44: ("Saareke 4", 1),
    45: ("Saareke 5", 1),
    46: ("Saareke 6", 1),
    47: ("Saareke 7", 1),
    48: ("Saareke 8", 1),
    49: ("Autokatos 1", None),
    50: ("Autokatos 2", None),
    51: ("Ulkovarasto", None),
}

FLOOR_NAMES = {0: "Kellari", 1: "Alakerta", 2: "Yläkerta"}


def floor_name(floor):
    return FLOOR_NAMES.get(floor, "Ulko")


def find_light_index(query):
    """Resolve a free-text light identifier to its Controls[] index.

    Accepts:
      - a numeric index (int or numeric string) → returned verbatim if known
      - a Finnish name or unique substring of one → matched case-insensitively

    Returns the integer index, or raises LookupError on unknown / ambiguous.
    """
    if isinstance(query, int):
        if query in LIGHT_LABELS:
            return query
        raise LookupError(f"Unknown light index {query}")

    s = str(query).strip()
    if not s:
        raise LookupError("Empty light identifier")

    if s.isdigit():
        idx = int(s)
        if idx in LIGHT_LABELS:
            return idx
        raise LookupError(f"Unknown light index {idx}")

    needle = s.lower()
    exact = [idx for idx, (name, _) in LIGHT_LABELS.items()
             if name.lower() == needle]
    if len(exact) == 1:
        return exact[0]

    partial = [(idx, name) for idx, (name, _) in LIGHT_LABELS.items()
               if needle in name.lower()]
    if len(partial) == 1:
        return partial[0][0]
    if len(partial) > 1:
        names = ", ".join(f"{name} (id={idx})" for idx, name in partial[:8])
        raise LookupError(
            f"Ambiguous light identifier '{query}' — matches: {names}"
        )
    raise LookupError(f"No light matches '{query}'")
