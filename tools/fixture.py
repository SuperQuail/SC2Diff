# -*- coding: utf-8 -*-
"""Synthetic StarCraft II document fixtures.

The real sample packages (community campaigns) are copyrighted and are deliberately not in
this repository, so anything that must run in CI needs a document it can build itself.  This
module produces a small but structurally complete .SC2Map: real MPQ v3 container, real
HET/BET/attributes/digest-block, mixed text + binary components, stable identity keys.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sc2mpq

XML = '<?xml version="1.0" encoding="utf-8"?>\n'

COMPONENTS = [
    ("ComponentList.SC2Components", (XML +
        '<Components>\n'
        '    <DataComponent Type="gada">GameData</DataComponent>\n'
        '    <DataComponent Type="info">DocumentInfo</DataComponent>\n'
        '    <DataComponent Type="trig">Triggers</DataComponent>\n'
        '</Components>\n').encode("utf-8")),
    ("DocumentInfo", (XML +
        '<DocInfo Type="Map" Name="Fixture" Author="sc2diff">\n'
        '    <Dependencies/>\n'
        '</DocInfo>\n').encode("utf-8")),
    ("Objects", (XML +
        '<PlacedObjects Version="27">\n'
        '    <ObjectUnit Id="1" Position="10,10,0" Rotation="0" Scale="1,1,1" UnitType="Marine" Player="1"/>\n'
        '    <ObjectUnit Id="2" Position="20,20,0" Rotation="1.5" Scale="1,1,1" UnitType="Medivac" Player="1"/>\n'
        '    <ObjectUnit Id="3" Position="30,30,0" Rotation="3" Scale="1,1,1" UnitType="SiegeTank" Player="2">\n'
        '        <Flag Index="UnitHidden" Value="1"/>\n'
        '    </ObjectUnit>\n'
        '</PlacedObjects>\n').encode("utf-8")),
    ("Base.SC2Data\\GameData\\UnitData.xml", (XML +
        '<Catalog>\n'
        '    <CUnit id="Marine">\n'
        '        <LifeMax value="45"/>\n'
        '        <BehaviorArray index="0" Link="Stimpack"/>\n'
        '    </CUnit>\n'
        '    <CUnit id="Medivac">\n'
        '        <LifeMax value="150"/>\n'
        '    </CUnit>\n'
        '</Catalog>\n').encode("utf-8")),
    ("Base.SC2Data\\GameData\\AbilData.xml", (XML +
        '<Catalog>\n'
        '    <CAbilEffectTarget id="Stimpack">\n'
        '        <Cost index="0" Link="Minerals" Value="10"/>\n'
        '    </CAbilEffectTarget>\n'
        '</Catalog>\n').encode("utf-8")),
    ("Triggers", (XML +
        '<TriggerData>\n'
        '    <Root>\n'
        '        <Item Type="Trigger" Id="AAAAAAAA"/>\n'
        '    </Root>\n'
        '    <Element Type="Trigger" Id="AAAAAAAA">\n'
        '        <Item Type="Event" Id="BBBBBBBB"/>\n'
        '    </Element>\n'
        '</TriggerData>\n').encode("utf-8")),
    ("MapScript.galaxy", b"// fixture map script\nvoid InitMap() {\n}\n"),
    ("zhCN.SC2Data\\LocalizedData\\GameStrings.txt",
     "FixtureName=Fixture Map\nDocInfo/Name=Fixture Map\n".encode("utf-8")),
    ("DocumentHeader", bytes(range(256)) * 8),
    ("t3HeightMap", bytes((i * 37 + 11) % 256 for i in range(8192))),
]


def build(path: str, extra: list = None) -> str:
    """Write a deterministic fixture document."""
    entries = list(COMPONENTS) + list(extra or [])
    sc2mpq.build_document(entries, path, hash_count=None, block_shift=5,
                          unknown08=0x10, attr_flags=sc2mpq.ATTR_CRC32 | sc2mpq.ATTR_MD5)
    return os.path.abspath(path)


def ensure_source(explicit: str = None, cache: str = "testdata/fixture.SC2Map") -> str:
    """Return a usable document: the caller's choice, else a real sample, else a fixture."""
    if explicit and os.path.exists(explicit):
        return os.path.abspath(explicit)
    sample = "testdata/samples/paiur01.SC2Map"
    if os.path.exists(sample):
        return os.path.abspath(sample)
    if not os.path.exists(cache):
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        build(cache)
    return os.path.abspath(cache)
