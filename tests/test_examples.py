"""The example lead form posts exactly the fields the demo form source reads."""

from __future__ import annotations

import re
from pathlib import Path

from firstcall.corpus.world import load_world

FORM = Path(__file__).resolve().parents[1] / "examples" / "form.html"


def test_the_example_form_fields_match_the_site_form_source() -> None:
    names = set(re.findall(r'<(?:input|textarea)[^>]* name="([a-z_]+)"', FORM.read_text()))
    source = load_world().source("site-form")
    assert names == set(source["field_mapping"].values()) | {source["honeypot_field"]}
    assert 'class="honeypot"' in FORM.read_text()
