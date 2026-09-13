"""The planner is pure, so its tests are plain data.

No database, no bucket, no mocks — if a test here ever needs one, something has
leaked into plan.py that does not belong there. Several of these check the
worked examples in DR-032 and DR-033 directly, so a change to the methodology
fails here rather than in a bucket three reaches later.
"""

import math

import pytest

from recon import plan as planner
from recon.plan import DAR_EXPONENT, DownstreamRun, Seed, chains, others


def plan(*args, others: float = math.inf, downstream_q_set=None, **kwargs):
    """plan(), with nothing capping the ceiling unless a test says otherwise.

    An unbounded `others` reads every downstream run, which is exactly the
    single ALT-D ceiling. Most tests here are about the floor, the grid, binding
    and seeds, none of which ALT-E changes, so they keep their original shape;
    the ALT-E section below passes `others` explicitly. Unless told otherwise,
    every discharge in the pool is one the downstream reach adopted.
    """
    downstream = args[2] if len(args) > 2 else kwargs["downstream"]
    if downstream_q_set is None:
        downstream_q_set = sorted({r.q for r in downstream})
    return planner.plan(*args, others=others, downstream_q_set=downstream_q_set, **kwargs)


def ds(q: int, wse: float, bc_value: float, bc_type: str = "KWSE") -> DownstreamRun:
    return DownstreamRun(q=q, wse=wse, bc_type=bc_type, bc_value=bc_value)


# A downstream reach at one discharge: its normal-depth run plus four stage
# libraries. Achieved stages run 225.4 -> 227.6, imposed 222.0 -> 225.0, and the
# two never coincide — which is the whole reason a plan carries both.
POOL = [
    ds(900, 225.1, 12000.0, "ND"),
    ds(900, 225.4, 222.0),
    ds(900, 226.1, 223.0),
    ds(900, 226.8, 224.0),
    ds(900, 227.6, 225.0),
]

SLOPE = 12000.0


# --- DR-033 ALT-B: the stage grid ---------------------------------------

@pytest.mark.parametrize("dz, expected", [
    (0.25, [224.25, 224.5, 224.75, 225.0, 225.25, 225.5, 225.75, 226.0,
            226.25, 226.5, 226.75, 227.0]),
    (1.0, [224.0, 225.0, 226.0, 227.0]),
    (2.0, [224.0, 226.0, 228.0]),
])
def test_grid_matches_dr033_worked_examples(dz, expected):
    """DR-033's own examples for a downstream range of 224.2 -> 227.1.

    Bounds are ROUNDED to the nearest increment, not floored and ceiled, which
    is why dz=1 starts at 224 and dz=2 reaches 228 — both outside the range.
    """
    pool = [ds(900, 224.2, 220.0), ds(900, 227.1, 223.0)]
    # Every target must bind, so give the pool a run at each expected stage.
    pool += [ds(900, z, z - 3.0) for z in expected]
    got = [s.z for s in plan([900], dz, pool, SLOPE).scenarios]
    assert got == pytest.approx(expected)


def test_grid_is_anchored_to_zero_not_to_the_reach():
    """A dz of 1 lands on whole metres regardless of where the floor sits."""
    pool = [ds(900, 585.4, 580.0)] + [ds(900, z, z - 5.0)
                                      for z in (585.0, 586.0, 587.0)]
    got = [s.z for s in plan([900], 1.0, pool, SLOPE).scenarios]
    assert got == pytest.approx([585.0, 586.0, 587.0])


# --- DR-032: the floor, and the ceiling with nothing capping it ----------

def test_uncapped_ceiling_is_the_highest_stage_anywhere_downstream():
    """ALT-D's single value, which ALT-E reduces to when nothing caps it."""
    result = plan([200, 900], 1.0, POOL, SLOPE)
    assert [c.wse for c in result.ceilings] == pytest.approx([227.6, 227.6])


def test_floor_rises_with_discharge_so_the_library_narrows():
    """A higher discharge has a higher floor, hence fewer stages."""
    # The high discharge simply never sat as low as the low one did, so its
    # pool starts at 225.6 and the stages below that have nothing to bind to.
    pool = [ds(200, z, z - 3.0) for z in (223.0, 224.0, 225.0, 226.0, 227.0)]
    pool += [ds(900, z, z - 3.0) for z in (225.6, 226.0, 227.0)]
    result = plan([200, 900], 1.0, pool, SLOPE)
    at = lambda q: [s.z for s in result.scenarios if s.q == q]
    assert at(200) == pytest.approx([223.0, 224.0, 225.0, 226.0, 227.0])
    assert at(900) == pytest.approx([226.0, 227.0])


def test_floor_uses_nearest_downstream_discharge_at_or_below():
    """Our q=900 takes its floor from the q=500 curve, not the q=1500 one."""
    pool = [ds(500, 225.0, 222.0), ds(500, 226.0, 223.0),
            ds(1500, 240.0, 235.0)]
    result = plan([900], 1.0, pool, SLOPE)
    assert min(s.z for s in result.scenarios) == pytest.approx(225.0)


def test_binding_is_not_restricted_to_the_discharge_that_set_the_floor():
    """Only the floor picks one downstream discharge; binding sees a range.

    Restricting the pool to that discharge would put the top of the envelope out
    of reach, since the downstream reach only reaches its highest stages at its
    highest flows. Here nothing caps the range, so the range is everything.
    """
    pool = [ds(500, 225.0, 222.0), ds(500, 226.0, 223.0),
            ds(1500, 240.0, 235.0)]
    result = plan([900], 1.0, pool, SLOPE)
    reached = {s.z: s.downstream.q for s in result.scenarios}
    assert reached[225.0] == 500
    assert reached[240.0] == 1500   # the ceiling, only available at q=1500


def test_small_tributary_can_still_reach_mainstem_flood_stages():
    """DR-033's extreme drainage-area ratio: low flow, high backwater.

    A trickle in the tributary coinciding with the mainstem in flood is a real
    combination, and the one this case exists to keep reachable.
    """
    mainstem = []
    for q, stages in [(400, [225.0, 226.0, 227.0]), (900, [228.0, 229.0, 230.0]),
                      (4500, [231.0, 232.0, 233.0])]:
        mainstem += [ds(q, w, w - 3.0) for w in stages]
    result = plan([5, 12], 1.0, mainstem, SLOPE)
    assert result.ceilings[0].wse == pytest.approx(233.0)
    # Every stage the mainstem ever produced is reachable at the tributary's
    # smallest discharge, not just the ones at the mainstem's lowest flow.
    assert [s.z for s in result.scenarios if s.q == 5] == pytest.approx(
        [225.0, 226.0, 227.0, 228.0, 229.0, 230.0, 231.0, 232.0, 233.0])
    assert result.skipped == ()


def test_ties_go_to_the_lower_discharge():
    """Two runs at the same stage: the smaller footprint wins, deterministically."""
    pool = [ds(400, 226.0, 223.0), ds(900, 226.0, 224.0)]
    result = plan([900], 1.0, pool, SLOPE)
    assert [s.downstream.q for s in result.scenarios] == [400]


def test_floor_clamps_when_downstream_has_nothing_at_or_below():
    """Downstream drains more area, so its discharges can all exceed ours."""
    pool = [ds(1500, 240.0, 235.0), ds(1500, 241.0, 236.0)]
    result = plan([200], 1.0, pool, SLOPE)
    assert [s.z for s in result.scenarios] == pytest.approx([240.0, 241.0])


def test_authored_upper_bound_can_only_lower_the_ceiling():
    low = plan([900], 1.0, POOL, SLOPE, kwse_upper_bound=226.0)
    assert low.ceilings[0].wse == pytest.approx(226.0)
    high = plan([900], 1.0, POOL, SLOPE, kwse_upper_bound=999.0)
    assert high.ceilings[0].wse == pytest.approx(227.6)


# --- DR-032 ALT-E: a ceiling per discharge -------------------------------

# The downstream reach drawn in KWSE_CEILING_PLAN.md §5–6: its discharge range
# and FIGURE_OTHERS come from a real test-network pair, its stages are made up.
FIGURE = (
    [ds(170, w, w - 3.0) for w in (42.9, 44.0, 45.4)]
    + [ds(650, w, w - 3.0) for w in (43.4, 44.5, 45.4)]
    + [ds(1400, w, w - 3.0) for w in (45.0, 46.1)]
    + [ds(2200, w, w - 3.0) for w in (45.9, 46.8)]
    + [ds(3080, w, w - 3.0) for w in (46.7, 47.4)]
)
FIGURE_OTHERS = 653.0


def rungs(result) -> dict[int, list[float]]:
    by_q: dict[int, list[float]] = {}
    for s in result.scenarios:
        by_q.setdefault(s.q, []).append(s.z)
    return by_q


def test_others_matches_the_dr032_worked_example():
    """This reach holds 800 of the downstream reach's 1000 km², and that reach is
    bounded at 4500: the other 20% of the area can deliver 0.2^0.7 of it."""
    assert others(800.0, 1000.0, 4500.0) == pytest.approx(1458.6, abs=0.1)


@pytest.mark.parametrize("own, below, q_upper, expected", [
    (14.7, 1567.0, 935.0, 928.9),      # small tributary: nearly all of D's flood
    (896.8, 1006.7, 3079.0, 653.3),    # two comparable contributors
    (1006.7, 1028.2, 3304.0, 220.4),   # this reach is almost the whole river
])
def test_others_on_test_network_pairs(own, below, q_upper, expected):
    assert others(own, below, q_upper) == pytest.approx(expected, abs=0.1)


def test_the_exponent_credits_the_rest_of_the_basin_with_more_than_its_area_share():
    """b < 1 is the safe side. Plain area ratio gives this pair 69 cms, which
    would lower its ceilings and delete stages that can really happen."""
    assert DAR_EXPONENT < 1.0
    linear = (1 - 1006.7 / 1028.2) * 3304.0
    assert others(1006.7, 1028.2, 3304.0) > 3 * linear


def test_equal_areas_add_nothing():
    """The downstream reach carries exactly what this one sends."""
    assert others(500.0, 500.0, 1000.0) == 0.0


def test_more_area_than_downstream_is_refused_not_clamped():
    """Drainage area only grows downstream; a zero here would hide bad inputs."""
    with pytest.raises(ValueError, match="only grows downstream"):
        others(72.0, 71.6, 52.0)


@pytest.mark.parametrize("own, below, q_upper",
                         [(0.0, 10.0, 5.0), (5.0, -1.0, 5.0), (5.0, 10.0, 0.0)])
def test_non_positive_inputs_are_refused(own, below, q_upper):
    with pytest.raises(ValueError, match="positive"):
        others(own, below, q_upper)


def test_plan_has_no_default_for_others():
    """No silent fallback to ALT-D: a caller that cannot say what others is fails."""
    with pytest.raises(TypeError):
        planner.plan([900], 1.0, POOL, SLOPE)


@pytest.mark.parametrize("bad", [-1.0, math.nan])
def test_plan_refuses_a_negative_or_nan_others(bad):
    with pytest.raises(ValueError, match="non-negative"):
        plan([900], 1.0, POOL, SLOPE, others=bad)


def test_the_cap_rounds_up_to_the_next_downstream_discharge():
    """§5: cap 150 + 653 = 803 falls between the downstream reach's 650 and 1400.
    It really can carry 803 but modelled nothing there, so its runs are read up
    to 1400 — reading only up to 650 would put the ceiling at 45.4."""
    at_150 = plan([150], 1.0, FIGURE, SLOPE, others=FIGURE_OTHERS).ceilings[0]
    assert at_150.cap == pytest.approx(803.0)
    assert at_150.read_q == 1400
    assert at_150.wse == pytest.approx(46.1)


def test_the_ladder_from_the_plan_document():
    """§6: the stages cut are the ones only the downstream reach's biggest
    floods produce, and at this reach's own flood nothing is cut."""
    q_set = [150, 650, 2780]
    capped = plan(q_set, 1.0, FIGURE, SLOPE, others=FIGURE_OTHERS)
    uncapped = plan(q_set, 1.0, FIGURE, SLOPE)
    assert rungs(capped) == {150: [43.0, 44.0, 45.0, 46.0],
                             650: [43.0, 44.0, 45.0, 46.0],
                             2780: [46.0, 47.0]}
    assert rungs(uncapped) == {150: [43.0, 44.0, 45.0, 46.0, 47.0],
                               650: [43.0, 44.0, 45.0, 46.0, 47.0],
                               2780: [46.0, 47.0]}
    assert (len(uncapped.scenarios), len(capped.scenarios)) == (12, 10)


def test_a_cap_exactly_on_a_downstream_discharge_reads_that_discharge():
    at_650 = plan([650], 1.0, FIGURE, SLOPE, others=0.0).ceilings[0]
    assert at_650.read_q == 650
    assert at_650.wse == pytest.approx(45.4)


def test_a_cap_above_the_largest_downstream_discharge_is_the_uncapped_plan():
    """The downstream library stops at its largest discharge, so this reach's
    own flood already reaches everything that library holds."""
    capped = plan([2780], 1.0, FIGURE, SLOPE, others=FIGURE_OTHERS)
    assert capped.ceilings[0].read_q == 3080
    assert capped.scenarios == plan([2780], 1.0, FIGURE, SLOPE).scenarios


# The downstream reach adopted 200 and 600; an older sweep also left a
# normal-depth run at 400, which carries no stage library.
LEFTOVER = [ds(200, 224.0, SLOPE, "ND"), ds(200, 226.0, 222.0), ds(200, 228.0, 223.0),
            ds(400, 225.0, SLOPE, "ND"),
            ds(600, 226.5, SLOPE, "ND"), ds(600, 229.0, 224.0)]


def test_a_cap_never_rounds_up_onto_a_leftover_discharge():
    """Cap 350 would round onto the leftover at 400, reading backwater only up to
    the adopted 200 — but the downstream reach's highest stage at 350 lies above
    that. It rounds onto the next ADOPTED discharge instead."""
    ceiling = plan([200], 1.0, LEFTOVER, SLOPE, others=150.0,
                   downstream_q_set=[200, 600]).ceilings[0]
    assert ceiling.read_q == 600
    assert ceiling.wse == pytest.approx(229.0)


def test_leftover_runs_below_the_read_discharge_are_still_candidates():
    """Below the cap a leftover only adds a water surface to bind to."""
    result = plan([200], 1.0, LEFTOVER, SLOPE, others=150.0, downstream_q_set=[200, 600])
    assert {s.z: s.downstream.q for s in result.scenarios}[225.0] == 400


def test_the_ceiling_is_the_highest_stage_up_to_the_read_discharge_not_at_it():
    """A reach's highest stage need not sit at its highest discharge: here the
    backwater run at 200 tops the one at 600, and both are within reach."""
    pool = [ds(200, 224.0, SLOPE, "ND"), ds(200, 230.0, 226.0),
            ds(600, 226.5, SLOPE, "ND"), ds(600, 229.0, 224.0)]
    ceiling = plan([200], 1.0, pool, SLOPE, others=150.0).ceilings[0]
    assert ceiling.read_q == 600
    assert ceiling.wse == pytest.approx(230.0)


def test_refuses_no_downstream_library_discharges():
    with pytest.raises(ValueError, match="no downstream library discharges"):
        plan([200], 1.0, LEFTOVER, SLOPE, others=0.0, downstream_q_set=[])


def test_refuses_a_library_discharge_with_no_runs():
    """Adopted means proved, so it always has a run; one without is out of step."""
    with pytest.raises(ValueError, match=r"\[800\] have no runs"):
        plan([200], 1.0, LEFTOVER, SLOPE, others=0.0, downstream_q_set=[200, 600, 800])


def test_binding_never_imposes_a_flood_above_the_read_discharge():
    """Stage 226 sits nearest the downstream q=900 run, but that reach cannot be
    at 900 while this one sends 200 and nothing else adds any — so it binds to
    the q=200 run at 226.4, still inside Δz/2."""
    pool = [ds(200, 225.0, 222.0), ds(200, 226.4, 223.0), ds(900, 226.0, 222.5)]
    capped = {s.z: s.downstream.q for s in plan([200], 1.0, pool, SLOPE, others=0.0).scenarios}
    uncapped = {s.z: s.downstream.q for s in plan([200], 1.0, pool, SLOPE).scenarios}
    assert capped[226.0] == 200
    assert uncapped[226.0] == 900


def test_ceilings_never_fall_as_discharge_rises():
    result = plan([150, 650, 1250, 2780], 1.0, FIGURE, SLOPE, others=FIGURE_OTHERS)
    stages = [c.wse for c in result.ceilings]
    assert stages == sorted(stages)


def test_every_discharge_records_its_ceiling_even_when_its_envelope_closes():
    result = plan([900], 1.0, [ds(900, 230.0, 226.0)], SLOPE,
                  kwse_upper_bound=220.0, others=0.0)
    assert result.scenarios == ()
    assert [(c.q, c.read_q, c.wse) for c in result.ceilings] == [(900, 900, 220.0)]


@pytest.mark.parametrize("extra", [0.0, 50.0, FIGURE_OTHERS, 5000.0])
@pytest.mark.parametrize("pool, q_set", [(FIGURE, [150, 650, 1250, 2780]),
                                         (POOL, [200, 900])])
def test_a_capped_plan_only_ever_drops_scenarios(pool, q_set, extra):
    """Why adopting ALT-E re-runs nothing: every (discharge, stage) it asks for
    is one the uncapped plan already asked for, so existing libraries still
    satisfy it."""
    capped = {(s.q, s.z) for s in plan(q_set, 1.0, pool, SLOPE, others=extra).scenarios}
    uncapped = {(s.q, s.z) for s in plan(q_set, 1.0, pool, SLOPE).scenarios}
    assert capped <= uncapped


# --- binding: achieved vs imposed ---------------------------------------

def test_target_binds_on_achieved_but_addresses_by_imposed():
    """The two numbers differ by metres; a plan must carry both."""
    result = plan([900], 1.0, POOL, SLOPE)
    bound = {s.z: (s.downstream.wse, s.downstream.bc_value) for s in result.scenarios}
    assert bound[226.0] == (226.1, 223.0)   # target 226, folder kwse=223.0
    assert bound[227.0] == (226.8, 224.0)   # target 227, folder kwse=224.0


def test_a_low_target_may_bind_to_the_downstream_normal_depth_run():
    """The candidate pool spans both of the downstream reach's tables."""
    result = plan([900], 1.0, POOL, SLOPE)
    assert result.scenarios[0].downstream.bc_type == "ND"


def test_target_with_no_run_inside_half_dz_is_skipped_not_run():
    """DR-033 calls these gaps in the downstream reach's own sampling."""
    pool = [ds(900, 224.0, 220.0), ds(900, 227.0, 223.0)]
    result = plan([900], 1.0, pool, SLOPE)
    assert [s.z for s in result.scenarios] == pytest.approx([224.0, 227.0])
    assert [s.z for s in result.skipped] == pytest.approx([225.0, 226.0])


def test_a_run_exactly_half_dz_away_still_binds():
    """The DR says 'within', and the grid rounding relies on that inclusiveness."""
    pool = [ds(900, 224.5, 220.0)]
    result = plan([900], 1.0, pool, SLOPE)
    assert [s.z for s in result.scenarios] == pytest.approx([224.0])


# --- the hotstart chain --------------------------------------------------

def test_every_discharge_is_rooted_in_this_reach_normal_depth_run():
    """No scenario starts dry: the first stage seeds from our own ND run."""
    pool = [ds(q, z, z - 3.0) for q in (200, 900)
            for z in (225.0, 226.0, 227.0)]
    result = plan([200, 900], 1.0, pool, SLOPE)
    first = {q: next(s for s in result.scenarios if s.q == q) for q in (200, 900)}
    assert first[200].seed == Seed(q=200, bc_type="ND", bc_value=SLOPE)
    assert first[900].seed == Seed(q=900, bc_type="ND", bc_value=SLOPE)


def test_later_stages_chain_from_the_stage_below_at_the_same_discharge():
    result = plan([900], 1.0, POOL, SLOPE)
    seeds = [(s.z, s.seed.bc_type, s.seed.bc_value) for s in result.scenarios]
    assert seeds[1:] == [(z, "KWSE", z - 1.0) for z, _, _ in seeds[1:]]


def test_a_seed_always_appears_earlier_in_the_list_than_its_user():
    """The job runs serially, so a seed must exist by the time it is named."""
    result = plan([200, 900], 1.0,
                  [ds(q, z, z - 3.0) for q in (200, 900)
                   for z in (225.0, 226.0, 227.0)], SLOPE)
    seen: set[tuple[int, float]] = set()
    for s in result.scenarios:
        if s.seed.bc_type == "KWSE":
            assert (s.seed.q, s.seed.bc_value) in seen
        seen.add((s.q, s.z))


def test_a_skipped_stage_is_never_named_as_a_seed():
    """Chaining from a gap would point the job at a file that is not there."""
    pool = [ds(900, 224.0, 220.0), ds(900, 226.0, 222.0), ds(900, 227.0, 223.0)]
    result = plan([900], 1.0, pool, SLOPE)
    assert 225.0 in [s.z for s in result.skipped]
    ran = {s.z for s in result.scenarios}
    for s in result.scenarios:
        if s.seed.bc_type == "KWSE":
            assert s.seed.bc_value in ran


# --- chains: what can run side by side ---------------------------------

def test_chains_split_the_plan_by_discharge_keeping_stage_order():
    pool = [ds(q, z, z - 3.0) for q in (200, 900) for z in (225.0, 226.0, 227.0)]
    result = plan([900, 200], 1.0, pool, SLOPE)
    got = [[(s.q, s.z) for s in chain] for chain in chains(result.scenarios)]
    assert got == [[(200, 225.0), (200, 226.0), (200, 227.0)],
                   [(900, 225.0), (900, 226.0), (900, 227.0)]]


def test_no_chain_seeds_from_another():
    """What lets each chain be its own job: every seed is inside its chain,
    or is this reach's normal-depth run."""
    pool = [ds(q, z, z - 3.0) for q in (200, 900) for z in (225.0, 226.0, 227.0)]
    for chain in chains(plan([200, 900], 1.0, pool, SLOPE).scenarios):
        own = {(s.q, s.z) for s in chain}
        for s in chain:
            assert s.seed.bc_type == "ND" or (s.seed.q, s.seed.bc_value) in own


def test_a_subset_keeps_its_order_and_drops_empty_discharges():
    """Chains are also built from what is left once existing scenarios are
    removed, so a discharge with nothing left has no chain at all."""
    pool = [ds(q, z, z - 3.0) for q in (200, 900) for z in (225.0, 226.0, 227.0)]
    scenarios = plan([200, 900], 1.0, pool, SLOPE).scenarios
    left = [s for s in scenarios if s.q == 200 and s.z != 226.0]
    assert [[(s.q, s.z) for s in c] for c in chains(left)] == [[(200, 225.0), (200, 227.0)]]
    assert chains([]) == ()


# --- refusals and edges --------------------------------------------------

def test_a_closed_envelope_yields_nothing_rather_than_failing():
    """At this discharge the downstream reach never sat below the ceiling."""
    pool = [ds(900, 230.0, 226.0)]
    assert plan([900], 1.0, pool, SLOPE, kwse_upper_bound=220.0).scenarios == ()


def test_refuses_a_non_positive_increment():
    with pytest.raises(ValueError, match="positive"):
        plan([900], 0.0, POOL, SLOPE)


def test_refuses_when_the_downstream_reach_has_no_runs():
    with pytest.raises(ValueError, match="no downstream runs"):
        plan([900], 1.0, [], SLOPE)


def test_same_inputs_give_the_same_plan_every_time():
    """Rule 7: the calculation is a function of its inputs."""
    assert plan([200, 900], 0.5, POOL, SLOPE) == plan([200, 900], 0.5, POOL, SLOPE)
