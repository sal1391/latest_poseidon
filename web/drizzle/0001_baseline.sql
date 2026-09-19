CREATE TABLE "conversations" (
	"id" uuid PRIMARY KEY NOT NULL,
	"user_sub" text NOT NULL,
	"title" text DEFAULT 'New chat' NOT NULL,
	"mode" text DEFAULT 'default',
	"state" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"archived" boolean DEFAULT false NOT NULL,
	CONSTRAINT "ck_conversations_mode" CHECK (mode = ANY (ARRAY['existing'::text, 'prospect'::text, 'default'::text]))
);
--> statement-breakpoint
ALTER TABLE "conversations" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "llm_calls" (
	"id" uuid PRIMARY KEY NOT NULL,
	"turn_run_id" uuid NOT NULL,
	"user_sub" text NOT NULL,
	"seq" integer NOT NULL,
	"provider" text NOT NULL,
	"model_id" text NOT NULL,
	"role" text NOT NULL,
	"prompt_version" text NOT NULL,
	"prompt_hash" text NOT NULL,
	"input_tokens" integer DEFAULT 0 NOT NULL,
	"output_tokens" integer DEFAULT 0 NOT NULL,
	"latency_ms" integer,
	"status" text NOT NULL,
	"error" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_llm_calls_turn_run_id_seq" UNIQUE("turn_run_id","seq"),
	CONSTRAINT "ck_llm_calls_status" CHECK (status = ANY (ARRAY['ok'::text, 'error'::text]))
);
--> statement-breakpoint
ALTER TABLE "llm_calls" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "memory_outbox" (
	"conversation_id" uuid PRIMARY KEY NOT NULL,
	"user_sub" text NOT NULL,
	"last_turn_at" timestamp with time zone NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"attempts" integer DEFAULT 0 NOT NULL,
	"last_error" jsonb,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_memory_outbox_status" CHECK (status = ANY (ARRAY['pending'::text, 'done'::text, 'failed'::text]))
);
--> statement-breakpoint
ALTER TABLE "memory_outbox" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "message_feedback" (
	"id" uuid PRIMARY KEY NOT NULL,
	"message_id" uuid NOT NULL,
	"run_id" uuid NOT NULL,
	"user_sub" text NOT NULL,
	"verdict" text,
	"comment" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_message_feedback_message_id_user_sub" UNIQUE("message_id","user_sub"),
	CONSTRAINT "ck_message_feedback_verdict" CHECK (verdict = ANY (ARRAY['up'::text, 'down'::text]))
);
--> statement-breakpoint
ALTER TABLE "message_feedback" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "messages" (
	"id" uuid PRIMARY KEY NOT NULL,
	"conversation_id" uuid NOT NULL,
	"user_sub" text NOT NULL,
	"role" text,
	"parts" jsonb NOT NULL,
	"turn_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_messages_role" CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text]))
);
--> statement-breakpoint
ALTER TABLE "messages" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "tool_calls" (
	"id" uuid PRIMARY KEY NOT NULL,
	"turn_run_id" uuid NOT NULL,
	"user_sub" text NOT NULL,
	"seq" integer NOT NULL,
	"tool" text NOT NULL,
	"server" text,
	"args" jsonb,
	"result_digest" jsonb,
	"status" text NOT NULL,
	"latency_ms" integer,
	"error" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_tool_calls_turn_run_id_seq" UNIQUE("turn_run_id","seq"),
	CONSTRAINT "ck_tool_calls_status" CHECK (status = ANY (ARRAY['ok'::text, 'error'::text]))
);
--> statement-breakpoint
ALTER TABLE "tool_calls" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "turn_run" (
	"id" uuid PRIMARY KEY NOT NULL,
	"kind" text DEFAULT 'chat_turn' NOT NULL,
	"conversation_id" uuid,
	"message_id" uuid,
	"user_sub" text NOT NULL,
	"client_turn_key" uuid,
	"turn_index" integer,
	"question" text,
	"mode" text,
	"parsed" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"answer_summary" text,
	"status" text NOT NULL,
	"input_tokens" integer DEFAULT 0 NOT NULL,
	"output_tokens" integer DEFAULT 0 NOT NULL,
	"latency_ms" integer,
	"trace_id" text,
	"error" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone,
	"redacted_at" timestamp with time zone,
	CONSTRAINT "uq_turn_run_user_sub_client_turn_key" UNIQUE("user_sub","client_turn_key"),
	CONSTRAINT "ck_turn_run_kind" CHECK (kind = ANY (ARRAY['chat_turn'::text, 'memory_update'::text])),
	CONSTRAINT "ck_turn_run_status" CHECK (status = ANY (ARRAY['running'::text, 'ok'::text, 'clarify'::text, 'error'::text]))
);
--> statement-breakpoint
ALTER TABLE "turn_run" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "user_memory" (
	"user_sub" text NOT NULL,
	"version" integer NOT NULL,
	"entries" jsonb NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "user_memory_pkey" PRIMARY KEY("user_sub","version"),
	CONSTRAINT "ck_user_memory_created_by" CHECK (created_by = ANY (ARRAY['user'::text, 'distiller'::text]))
);
--> statement-breakpoint
ALTER TABLE "user_memory" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "user_profile" (
	"user_sub" text PRIMARY KEY NOT NULL,
	"system_instruction" text DEFAULT '' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "user_profile" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "llm_calls" ADD CONSTRAINT "llm_calls_turn_run_id_fkey" FOREIGN KEY ("turn_run_id") REFERENCES "public"."turn_run"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "memory_outbox" ADD CONSTRAINT "memory_outbox_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "message_feedback" ADD CONSTRAINT "message_feedback_message_id_fkey" FOREIGN KEY ("message_id") REFERENCES "public"."messages"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "message_feedback" ADD CONSTRAINT "message_feedback_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "public"."turn_run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "messages" ADD CONSTRAINT "messages_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tool_calls" ADD CONSTRAINT "tool_calls_turn_run_id_fkey" FOREIGN KEY ("turn_run_id") REFERENCES "public"."turn_run"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "ix_conversations_user_recency" ON "conversations" USING btree ("user_sub","updated_at" DESC NULLS FIRST,"id" DESC NULLS FIRST);--> statement-breakpoint
CREATE INDEX "ix_llm_calls_turn_run_id_seq" ON "llm_calls" USING btree ("turn_run_id","seq");--> statement-breakpoint
CREATE INDEX "ix_messages_conversation_order" ON "messages" USING btree ("conversation_id","created_at","id");--> statement-breakpoint
CREATE INDEX "ix_tool_calls_turn_run_id_seq" ON "tool_calls" USING btree ("turn_run_id","seq");--> statement-breakpoint
CREATE INDEX "ix_turn_run_conversation_id_turn_index" ON "turn_run" USING btree ("conversation_id","turn_index");--> statement-breakpoint
CREATE INDEX "ix_turn_run_created_at" ON "turn_run" USING btree ("created_at");--> statement-breakpoint
CREATE POLICY "conversations_owner" ON "conversations" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "llm_calls_admin_read" ON "llm_calls" AS PERMISSIVE FOR SELECT TO "poseidon_admin" USING (true);--> statement-breakpoint
CREATE POLICY "llm_calls_owner" ON "llm_calls" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "memory_outbox_worker" ON "memory_outbox" AS PERMISSIVE FOR ALL TO "poseidon_worker" USING (true) WITH CHECK (true);--> statement-breakpoint
CREATE POLICY "memory_outbox_owner" ON "memory_outbox" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "message_feedback_admin_read" ON "message_feedback" AS PERMISSIVE FOR SELECT TO "poseidon_admin" USING (true);--> statement-breakpoint
CREATE POLICY "message_feedback_owner" ON "message_feedback" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "messages_owner" ON "messages" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "tool_calls_admin_read" ON "tool_calls" AS PERMISSIVE FOR SELECT TO "poseidon_admin" USING (true);--> statement-breakpoint
CREATE POLICY "tool_calls_owner" ON "tool_calls" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "turn_run_admin_read" ON "turn_run" AS PERMISSIVE FOR SELECT TO "poseidon_admin" USING (true);--> statement-breakpoint
CREATE POLICY "turn_run_owner" ON "turn_run" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "user_memory_owner" ON "user_memory" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));--> statement-breakpoint
CREATE POLICY "user_profile_owner" ON "user_profile" AS PERMISSIVE FOR ALL TO public USING (user_sub = current_setting('app.user_sub', true)) WITH CHECK (user_sub = current_setting('app.user_sub', true));