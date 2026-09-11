"""The planner is pure, so its tests are plain data.

No database, no bucket, no mocks — if a test here ever needs one, something has
leaked into plan.py that does not belong there. Several of these check the
worked examples in DR-032 and DR-033 directly, so a change to the methodology
fails here rather than in a bucket three reaches later.
"""

import pytest

from recon.plan import DownstreamRun, Seed, plan


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


# --- DR-032 ALT-D: the envelope -----------------------------------------

def test_ceiling_is_one_value_for_every_discharge():
    """Highest achieved stage anywhere downstream, not per discharge."""
    result = plan([900], 1.0, POOL, SLOPE)
    assert result.ceiling == pytest.approx(227.6)


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
    """Only the floor picks one downstream discharge; binding sees them all.

    Restricting the pool would put the top of the envelope out of reach, since
    the downstream reach only reaches its highest stages at its highest flows.
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
    assert result.ceiling == pytest.approx(233.0)
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
    assert low.ceiling == pytest.approx(226.0)
    high = plan([900], 1.0, POOL, SLOPE, kwse_upper_bound=999.0)
    assert high.ceiling == pytest.approx(227.6)


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
