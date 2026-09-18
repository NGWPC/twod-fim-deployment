"""Tests for ND resolution."""

from psycopg.types.range import Range

from recon.observe import adopt

WANTED = {
    "ld_q_max_depth_increase_range": Range(0.75, 1.25),
    "ld_q_median_depth_increase_range": Range(0.25, 0.5),
    "ld_q_flooded_area_prcnt_increase_range": Range(10, 15),
    "q_grid_resolution": 10,
}


def entry(q: int, max_depth: float, median_depth: float = 0.0,
          flooded_area: float = 1.0) -> dict:
    return {"q": q, "max_depth": max_depth, "median_depth": median_depth,
            "flooded_area": flooded_area}


def depths(*pairs: tuple[int, float]) -> list[dict]:
    return [entry(q, d) for q, d in pairs]


def test_a_library_that_already_fits_is_adopted_whole():
    library = depths((100, 2.0), (200, 3.0), (300, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200, 300]
    assert result.holes == [] and result.expected == []


def test_a_redundant_scenario_is_passed_over():
    library = depths((100, 2.0), (150, 2.2), (200, 3.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert result.passed_over == 1


def test_a_load_bearing_scenario_is_kept():
    library = depths((100, 2.0), (200, 3.0), (300, 4.5))
    assert adopt(library, WANTED).q_set == [100, 200, 300]


def test_the_search_steps_over_a_dead_end():
    library = depths((100, 2.0), (200, 3.0), (250, 2.6), (300, 4.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200, 300]
    assert 250 not in result.q_set


def test_a_backwards_reading_is_just_a_step_too_small():
    library = depths((100, 2.0), (200, 1.8), (300, 3.0))
    assert adopt(library, WANTED).q_set == [100, 300]


def test_a_step_over_a_ceiling_is_only_allowed_between_neighbours():
    library = depths((100, 2.0), (150, 2.8), (200, 3.6), (300, 5.0))
    result = adopt(library, WANTED)
    assert result.q_set == [100, 150, 200, 300], "must not leap 100 to 300"


def test_a_step_over_a_ceiling_across_skipped_grid_values_leaves_no_library():
    result = adopt(depths((100, 2.0), (200, 4.0)), WANTED)
    assert result.holes and "no route" in result.holes[0]


def test_a_step_over_a_ceiling_a_single_grid_step_apart_is_accepted():
    result = adopt(depths((100, 2.0), (110, 4.0)), WANTED)
    assert result.q_set == [100, 110]
    assert result.holes == []
    assert any("faster than its discharge grid" in n for n in result.expected)


def test_without_a_grid_only_storage_adjacency_is_left():
    wanted = {**WANTED, "q_grid_resolution": None}
    result = adopt(depths((100, 2.0), (200, 4.0)), wanted)
    assert result.q_set == [100, 200], "adjacent in storage, so tolerated"
    assert result.holes == []


def test_median_depth_alone_can_carry_a_step():
    library = [entry(100, 2.0, 0.50, 1.00), entry(200, 2.1, 0.85, 1.02)]
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert result.holes == [] and result.expected == []


def test_flooded_area_alone_can_carry_a_step():
    library = [entry(100, 2.0, 0.50, 1.00), entry(200, 2.1, 0.55, 1.12)]
    result = adopt(library, WANTED)
    assert result.q_set == [100, 200]
    assert result.holes == [] and result.expected == []


def test_any_criterion_over_its_ceiling_vetoes_a_step():
    library = [
        entry(100, 2.00, 0.50, 1.00),
        entry(200, 2.50, 0.60, 1.08),
        entry(300, 3.00, 0.70, 1.20),
    ]
    assert adopt(library, WANTED).q_set == [100, 200, 300]


def test_an_unauthored_band_takes_no_part():
    wanted = dict.fromkeys(WANTED, None)
    library = depths((100, 2.0), (150, 2.01), (200, 9.0))
    result = adopt(library, wanted)
    assert result.q_set == [100, 150, 200]
    assert result.holes == [] and result.expected == []


def test_a_single_scenario_library_is_returned_untouched():
    result = adopt(depths((100, 2.0)), WANTED)
    assert result.q_set == [100] and result.holes == []


def test_adoption_never_reorders_or_invents_discharges():
    library = depths(*[(q, 2.0 + q / 120) for q in (100, 140, 190, 250, 320, 400)])
    result = adopt(library, WANTED)
    assert result.q_set == sorted(result.q_set)
    assert set(result.q_set) <= {e["q"] for e in library}
    assert result.q_set[0] == 100 and result.q_set[-1] == 400


def test_a_step_too_small_is_expected_not_a_finding():
    result = adopt(depths((100, 2.0), (200, 3.0), (210, 3.4), (310, 4.4)), WANTED)
    assert result.q_set == [100, 200, 210, 310]
    assert result.holes == []
    assert any("no line clears a floor" in n for n in result.expected)
