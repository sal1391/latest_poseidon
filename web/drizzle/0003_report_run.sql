CREATE TABLE "report_run" (
	"id" uuid PRIMARY KEY NOT NULL,
	"definition_id" uuid,
	"perspective" text NOT NULL,
	"office_column" text,
	"office_value" text,
	"period_current_start" date NOT NULL,
	"period_current_end" date NOT NULL,
	"period_prior_start" date NOT NULL,
	"period_prior_end" date NOT NULL,
	"window_kind" text NOT NULL,
	"basis" text DEFAULT 'prior_month' NOT NULL,
	"status" text NOT NULL,
	"payload_version" integer NOT NULL,
	"payload" jsonb NOT NULL,
	"html" "bytea",
	"pdf" "bytea",
	"narrative_grounded" boolean NOT NULL,
	"source" text NOT NULL,
	"created_by" text NOT NULL,
	"conversation_id" uuid,
	"superseded_by" uuid,
	"generated_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_report_run_perspective" CHECK (perspective = ANY (ARRAY['customer'::text, 'supplier'::text, 'book'::text])),
	CONSTRAINT "ck_report_run_office_column" CHECK (office_column IS NULL OR office_column = ANY (ARRAY['CUSTOMER_TEAM_NAME'::text, 'PRIMARY_SUPPLY_TEAM_OFFICE'::text])),
	CONSTRAINT "ck_report_run_window_kind" CHECK (window_kind = ANY (ARRAY['complete'::text, 'mtd'::text])),
	CONSTRAINT "ck_report_run_status" CHECK (status = ANY (ARRAY['ok'::text, 'error'::text]))
);
--> statement-breakpoint
ALTER TABLE "conversations" DROP CONSTRAINT "ck_conversations_mode";--> statement-breakpoint
ALTER TABLE "conversations" ADD CONSTRAINT "ck_conversations_mode" CHECK (mode = ANY (ARRAY['existing'::text, 'prospect'::text, 'default'::text, 'report'::text]));
--> statement-breakpoint
-- Phase 17 D37/D65/D69: a shared org document. No ENABLE/FORCE ROW LEVEL SECURITY, no policy.
-- Reads by any Poseidon:Sales user; writes by the app role on behalf of the generating user.
-- No UPDATE this phase (Phase 18 adds superseded_by writes with its own grant).
GRANT SELECT, INSERT ON report_run TO poseidon_app;
