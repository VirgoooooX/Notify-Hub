from __future__ import annotations

from plugins.builtin.fabrizio_hwg_monitor.matcher import match_hwg


def test_match_hwg_success() -> None:
    assert match_hwg("João Pedro to Chelsea, here we go!")
    assert match_hwg("Here we go! Done deal.")
    assert match_hwg("Here-we-go: agreement reached.")
    assert match_hwg("HERE WE GO")
    assert match_hwg("HERE—WE—GO")
    assert match_hwg("HWG")
    assert match_hwg("#HWG")
    assert match_hwg("hwg")
    assert match_hwg("Not a here we go yet")


def test_match_hwg_failure() -> None:
    assert not match_hwg("showgo")
    assert not match_hwg("hwgroup")
    assert not match_hwg("Regular transfer rumor without key phrase.")
