-- 08_views.sql
-- Per guide.md, what can be derived is not stored

--   q_set         : distinct library discharges (kwse runs reuse nd discharges, so
--                   distinct q over the reach's runs is the library, DR-030).
--   ds_min/max_kwse: range of known WSE actually run; nd runs have kwse_m NULL and
--                   so are naturally ignored by min()/max().
CREATE OR REPLACE VIEW current_state_realized AS
SELECT
    r.reach_id,
    array_agg(DISTINCT r.q_cms ORDER BY r.q_cms) AS q_set,
    count(DISTINCT r.q_cms)                       AS n_discharges,
    min(r.kwse_m)                                 AS ds_min_kwse,
    max(r.kwse_m)                                 AS ds_max_kwse
FROM runs r
GROUP BY r.reach_id;

