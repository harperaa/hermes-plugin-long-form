"""The content-pipeline prompt carries the plain-naming rule, and the prior
default stays byte-identical so the dashboard migrator can upgrade jobs."""
from pathlib import Path

import cli

ROOT = Path(__file__).resolve().parent.parent


def test_naming_rule_is_part_of_the_default_prompt():
    assert cli.PIPELINE_PROMPT.startswith(cli.PIPELINE_PROMPT_V5)
    rule = cli.PIPELINE_PROMPT[len(cli.PIPELINE_PROMPT_V5):]
    assert rule.startswith(" NAMING:")
    for needle in ("viewer's problem", "no folder slug ending in -os", "legacy naming",
                   "no invented acronyms", "at most one topic per run"):
        assert needle in rule, needle


def test_previous_default_is_preserved_for_the_migrator():
    # V5 = V4 + the voice clause, exactly what unmodified jobs hold today
    assert cli.PIPELINE_PROMPT_V5.startswith(cli.PIPELINE_PROMPT_V4)
    assert cli.PIPELINE_PROMPT_V5.endswith("transcripts or the insight base.")
    assert "NAMING" not in cli.PIPELINE_PROMPT_V5
    api = (ROOT / "dashboard" / "plugin_api.py").read_text()
    assert "mod.PIPELINE_PROMPT_V5" in api


def test_skills_carry_the_rule_too():
    gap = (ROOT / "skills" / "youtube-gap-finder" / "SKILL.md").read_text()
    assert "#### Naming topics (all modes)" in gap and "must not end in `-os`" in gap
    creator = (ROOT / "skills" / "youtube-content-creator" / "SKILL.md").read_text()
    assert "**Titles and names.**" in creator
