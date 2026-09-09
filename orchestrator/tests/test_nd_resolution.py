"""Adoption is pure, so its tests are plain data.

Storage holds whatever has been written there, from however many sweeps. None of
that is consulted: every scenario is judged only on the three readings in its own
manifest, against the authored bands, by the same rule the sweep applies to a
trial. The search is over every pair rather than consecutive ones, so it can step
over a scenario that leads nowhere instead of committing to it.
"""

from psycopg.types.range import Range

from recon.observe import adopt

# Bands as author_intent writes them: depths in metres, area as a percentage of
# the earlier scenario's own area.
WANTED = {
    "ld_q_max_depth_increase_range": Range(0.75, 1.25),
    "ld_q_median_depth_increase_range": Range(0.25, 0.5),
    "ld_q_flooded_area_prcnt_increase_range": Range(10, 15),
}


def entry(q: int, max_depth: float, median_depth: float = 0.0,
          flooded_area: float = 1.0) -> dict:
    return {"q": q, "max_depth": max_depth, "median_depth": median_depth,
            "flooded_area": flooded_area}


def depths(*pairs: tuple[int, float]) -> list[dict]:
    """A library that only exercises the max-depth band: the other two are held
    flat, so they never reach a floor and never breach a ceiling."""
    return [entry(q, d) for q, d in pairs]


def test_a_library_that_already_fits_is_adopted_whole():
    library = depths((100, 2.0), (200, 3.0), (300, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200, 300]
    assert result.holes == [] and result.notes == []


def test_a_redundant_scenario_is_passed_over():
    """150 is too similar to 100, and 100 to 200 is a good step on its own, so
    keeping 150 would cost a KWSE stage grid for nothing."""
    library = depths((100, 2.0), (150, 2.2), (200, 3.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert any("passed over" in n for n in result.notes)


def test_a_load_bearing_scenario_is_kept():
    """Dropping 200 leaves a step of 2.5, past the ceiling, so it has to stay."""
    library = depths((100, 2.0), (200, 3.0), (300, 4.5))
    assert adopt(library, WANTED).q_set == [100, 200, 300]


def test_the_search_steps_over_a_dead_end():
    """250 is reachable from 100, but nothing legal leaves it: 250 to 300 is too
    big and they are not neighbours. A left-to-right walk taking the furthest
    legal step would land on it and be stuck. Looking at every pair does not."""
    library = depths((100, 2.0), (200, 3.0), (250, 2.6), (300, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200, 300]
    assert 250 not in result.q_set


def test_a_backwards_reading_is_just_a_step_too_small():
    """A scenario that reads lower than the one before it is not a special case.
    It failed to reach any floor, which is reject_low like any other."""
    library = depths((100, 2.0), (200, 1.8), (300, 3.0))
    assert adopt(library, WANTED).q_set == [100, 300]


def test_a_step_over_a_ceiling_is_only_allowed_between_neighbours():
    """Without this the cheapest answer is one stride from bottom to top, since
    fewer scenarios always wins on count. 150 and 200 sit in the gap, so the
    search has to route through them."""
    library = depths((100, 2.0), (150, 2.8), (200, 3.6), (300, 5.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 150, 200, 300], "must not leap 100 to 300"


def test_a_gap_wider_than_the_minimum_step_is_a_hole():
    """Neighbours 100 cms apart whose step breaks a ceiling: a finer scenario
    would have closed it, so the library is unfinished."""
    library = depths((100, 2.0), (200, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert len(result.holes) == 1 and "coarser than intent" in result.holes[0]


def test_a_gap_at_the_minimum_step_is_not_a_hole():
    """Neighbours 10 cms apart that still overshoot: the reach changes faster
    than the smallest step allowed, and no re-run improves on it."""
    library = depths((100, 2.0), (110, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 110]
    assert result.holes == []
    assert any("faster than the smallest step" in n for n in result.notes)


def test_median_depth_alone_can_carry_a_step():
    """Acceptance needs only ONE criterion inside its band. Here max depth and
    flooded area both fall short of their floors and the step still stands."""
    library = [entry(100, 2.0, 0.50, 1.00), entry(200, 2.1, 0.85, 1.02)]
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert result.holes == [] and result.notes == []


def test_flooded_area_alone_can_carry_a_step():
    """The same, carried by the third criterion. This is the common case low in
    a reach's range, where area moves long before either depth does."""
    library = [entry(100, 2.0, 0.50, 1.00), entry(200, 2.1, 0.55, 1.12)]
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert result.holes == [] and result.notes == []


def test_any_criterion_over_its_ceiling_vetoes_a_step():
    """Max depth is a perfect +1.00, but flooded area moved 20% against a 15%
    ceiling. reject_high wins, so 300 cannot follow 100 directly."""
    library = [
        entry(100, 2.00, 0.50, 1.00),
        entry(200, 2.50, 0.60, 1.08),
        entry(300, 3.00, 0.70, 1.20),
    ]
    assert adopt(library, WANTED).q_set == [100, 200, 300]


def test_an_unauthored_band_takes_no_part():
    """NULL on both desired_state and the defaults row. With nothing authored at
    all there is no resolution to judge, and the library stands as it is."""
    wanted = dict.fromkeys(WANTED, None)
    library = depths((100, 2.0), (150, 2.01), (200, 9.0))
    result = adopt(library, wanted)
    assert result.q_set == [100, 150, 200]
    assert result.holes == [] and result.notes == []


def test_a_single_scenario_library_is_returned_untouched():
    result = adopt(depths((100, 2.0)), WANTED)
    assert result.q_set == [100] and result.holes == []


def test_adoption_never_reorders_or_invents_discharges():
    library = depths(*[(q, 2.0 + q / 120) for q in (100, 140, 190, 250, 320, 400)])
    result = adopt(library, WANTED)
    assert result.q_set == sorted(result.q_set)
    assert set(result.q_set) <= {e["q"] for e in library}
    assert result.q_set[0] == 100 and result.q_set[-1] == 400
