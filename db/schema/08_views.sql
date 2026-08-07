-- 08_views.sql
-- Per guide.md, what can be derived is not stored
-- q_set / n_discharges: distinct library discharges for THIS reach (DR-030).
-- max_kwse		   : highest known-WSE actually run on THIS reach (nd runs have
--			     kwse_m NULL and so are ignored by max()).
-- ds_r_max_us_wse : the DOWNSTREAM reach's upstream WSEL (us_wse) max, which
--			     bounds this reach's KWSE library. NULL at
--			     terminals or until the downstream reach has runs.
-- Since it is a view, we drop it first and then create it.
DROP VIEW IF EXISTS current_state_realized;

CREATE VIEW current_state_realized AS
SELECT
    r.reach_id,
    array_agg(DISTINCT r.q_cms ORDER BY r.q_cms) AS q_set,
    count(DISTINCT r.q_cms) AS n_discharges,
    max(r.kwse) AS max_kwse,
    (
        SELECT
            max(d.us_wse)
        FROM
            runs d
        WHERE
            d.reach_id = rn.reach_to_id
    ) AS ds_r_max_us_wse
FROM
    runs r
    JOIN reach_network rn ON rn.reach_id = r.reach_id
GROUP BY
    r.reach_id,
    rn.reach_to_id;

COMMENT ON VIEW current_state_realized IS 'Current state of realized runs for each reach';
