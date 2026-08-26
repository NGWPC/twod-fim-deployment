-- 04_materialized_models.sql
--
-- Whether each reach's model intent has been materialized.
--
-- Not an inventory of storage. S3 is the inventory, and it is queried when
-- someone wants one. This table answers a narrower question: the reach's
-- effective intent implies an identity, the identity implies an address, and
-- something either is or is not at that address. A model built from other
-- inputs may well sit alongside it in the bucket; it is a previous intent's
-- leftovers, not part of this reach's state.
--
-- A row says three things at once:
--   it exists         something was found at the address intent implies
--   reconciled at N   applied_revision, the intent revision it was satisfying
--   as of T           confirmed_at, because this is a cache of a storage lookup
--
-- Existence and reconciled are separate claims. A row is only ever created when
-- intent is satisfied — observe looks for the address intent implies, so finding
-- something there means it matches by construction — but observe does not run on
-- every check, so between an intent change and the next observation the row
-- still describes the old intent. applied_revision is what makes that readable
-- without looking at storage again.
--
-- Every column earns its place the same way: the loop needs it to decide the
-- gap, or to construct the work that closes it. Anything else belongs in the
-- manifest, which is the full record by design.
CREATE TABLE IF NOT EXISTS materialized_models(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- Compared against the identity the reconciler predicts from effective
    -- intent. That comparison IS the model gap: differ, or absent, and a build
    -- is owed. No revision bookkeeping needed for identity-affecting changes —
    -- change an input, the prediction moves, the comparison fails.
    identity_hash char(8) NOT NULL CONSTRAINT materialized_models_identity_hash_chk CHECK (identity_hash ~ '^[0-9a-f]{8}$'),
    -- The realization: which extent this recipe was built over. Read from what
    -- was found, never predicted — it is derived from the computed DEM clip, so
    -- the reconciler cannot know it in advance and does not need to. It is here
    -- because the domain-coverage check reads it once model_domain is authored.
    domain_code text NOT NULL CONSTRAINT materialized_models_domain_code_chk CHECK (domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    -- Separator is '_', matching the folder name in storage
    -- (guide.md: 5f14368c_N350S296E449W355).
    model_id text GENERATED ALWAYS AS (identity_hash || '_' || domain_code) STORED,
    -- The intent revision this materialization was confirmed to satisfy.
    -- It lives here, with the thing it makes a claim about, so that deleting the
    -- row deletes the claim in the same statement. Held elsewhere it outlives
    -- its subject: a model removed from storage would leave behind a record
    -- saying the reach was satisfied, and nothing would notice.
    applied_revision integer NOT NULL,
    -- When storage last confirmed this. A cache without a staleness marker is a
    -- cache you cannot reason about.
    confirmed_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE materialized_models IS 'Whether each reach model intent is materialized: what was found at the address intent implies, which revision it satisfied, and when that was last confirmed. A cache of a storage lookup, not an inventory.';

COMMENT ON COLUMN materialized_models.identity_hash IS 'Identity found at the predicted address. Compared against the prediction; differing or absent is the model gap.';

COMMENT ON COLUMN materialized_models.domain_code IS 'Realized extent, read from what was found. Not predictable — it comes from the computed DEM clip — and not needed to answer whether the recipe is materialized.';

COMMENT ON COLUMN materialized_models.model_id IS 'Generated identity_hash_domain_code; the model folder name in storage.';

COMMENT ON COLUMN materialized_models.applied_revision IS 'Intent revision this materialization satisfied. Co-located with its subject so deletion retracts the claim automatically.';

COMMENT ON COLUMN materialized_models.confirmed_at IS 'When storage last confirmed this row.';
