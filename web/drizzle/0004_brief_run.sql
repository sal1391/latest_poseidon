CREATE TABLE "brief_run" (
	"id" uuid PRIMARY KEY NOT NULL,
	"kind" text NOT NULL,
	"subject" text NOT NULL,
	"title" text NOT NULL,
	"anchor_date" date NOT NULL,
	"status" text NOT NULL,
	"markdown" text NOT NULL,
	"html" "bytea",
	"pdf" "bytea",
	"pdf_error" text,
	"created_by" text NOT NULL,
	"conversation_id" uuid,
	"generated_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_brief_run_kind" CHECK (kind = ANY (ARRAY['existing_customer'::text, 'new_prospect'::text])),
	CONSTRAINT "ck_brief_run_status" CHECK (status = ANY (ARRAY['ok'::text, 'error'::text]))
);
--> statement-breakpoint
-- D83: a shared org document like report_run (D65/D69). No ENABLE/FORCE ROW LEVEL SECURITY, no policy.
-- Reads by any Poseidon:Sales user; writes by the app role on behalf of the generating user.
GRANT SELECT, INSERT ON brief_run TO poseidon_app;
