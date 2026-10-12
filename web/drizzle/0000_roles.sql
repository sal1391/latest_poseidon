-- Phase 16 Task 7. Roles are CLUSTER-wide: a plain CREATE ROLE fails when a
-- sibling database in the same cluster already created one, so each is
-- idempotent -- the same DO-block discipline migrations 0004, 0005 and 0009
-- used. schema.ts declares them with pgRole(...).existing().
DO $$ BEGIN CREATE ROLE poseidon_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE ROLE poseidon_admin NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE ROLE poseidon_worker NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
