"""example.aoi_config.jsonc is the documentation of an AOI config, so it must stay complete.

It lists every key aoi_config.py accepts, each with what leaving it out does. A
key added to aoi_config.py without an entry here would be an option nobody can
find, so the example is checked against aoi_config.KEYS rather than trusted to
keep up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import aoi_config  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[2] / "example.aoi_config.jsonc"


def test_the_example_loads_as_an_aoi_config():
    loaded = aoi_config.load(str(EXAMPLE))
    assert loaded["network"].endswith("<identity_hash>/network.gpkg")


def test_the_example_names_every_key_an_aoi_config_accepts():
    """Active or commented out, every key appears, so every option is documented."""
    text = EXAMPLE.read_text()
    missing = sorted(key for key in aoi_config.KEYS if f'"{key}"' not in text)
    assert not missing, f"example.aoi_config.jsonc does not document: {missing}"


def test_comments_are_stripped_but_urls_in_strings_survive():
    text = '{\n  // a comment\n  "dem_source": "https://example.org/dem.vrt" // trailing\n}'
    assert aoi_config.strip_comments(text).count("https://example.org/dem.vrt") == 1
    assert "comment" not in aoi_config.strip_comments(text)
