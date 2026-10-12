// The migration SOURCE for the application schema (decision Q3, applied in
// Phase 16). `drizzle-kit generate` reads this file; `web/drizzle/` is what it
// produced. Hand-maintained from here on.
//
// Provenance: columns, defaults, CHECKs, foreign keys and unique constraints
// were introspected from an Alembic-migrated database by drizzle-kit 0.31.10
// (Phase 15). Policies, index definitions and roles were RE-DERIVED BY HAND
// from backend/migrations/versions/0003-0010 in Phase 16 Task 6, because the
// introspection dropped the USING/WITH CHECK predicate on five tables and
// rotated index operator classes. Never re-pull over this file.
//
// What lives OUTSIDE this file, in web/drizzle/0000_roles.sql and
// web/drizzle/0002_grants_force_rls.sql, because Drizzle cannot express it:
// idempotent role creation (roles are cluster-wide), FORCE ROW LEVEL
// SECURITY, every GRANT, and the two GRANT ... TO CURRENT_USER memberships
// from migrations 0009 and 0010.
//
// The `synthetic` schema (migration 0002) is Python's alone and is created by
// backend/poseidon/scripts/seed_synthetic.py, not here.
//
// Tripwires: web/src/db/migrations.test.ts (generate produces nothing new)
// and backend/tests/test_rls_policies.py (-m pg) against a Drizzle-migrated
// database.

import { pgTable, index, foreignKey, unique, pgPolicy, pgRole, check, uuid, text, integer, jsonb, timestamp, boolean, primaryKey } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"

// Created by web/drizzle/0000_roles.sql with idempotent DO blocks (migrations
// 0004, 0005, 0009). `.existing()` keeps drizzle-kit from emitting CREATE ROLE.
export const poseidonApp = pgRole("poseidon_app").existing();
export const poseidonAdmin = pgRole("poseidon_admin").existing();
export const poseidonWorker = pgRole("poseidon_worker").existing();

// Migrations 0004:_OWNER_PREDICATE, verbatim. Postgres stores it as
// `(user_sub = current_setting('app.user_sub'::text, true))`; both spellings
// compare equal in pg_policies, which backend/tests/test_rls_policies.py pins.
export const OWNER_PREDICATE = sql`user_sub = current_setting('app.user_sub', true)`;

const owner = (name: string) =>
	pgPolicy(`${name}_owner`, { as: "permissive", for: "all", using: OWNER_PREDICATE, withCheck: OWNER_PREDICATE });
const adminRead = (name: string) =>
	pgPolicy(`${name}_admin_read`, { as: "permissive", for: "select", to: poseidonAdmin, using: sql`true` });

export const toolCalls = pgTable("tool_calls", {
	id: uuid().primaryKey().notNull(),
	turnRunId: uuid("turn_run_id").notNull(),
	userSub: text("user_sub").notNull(),
	seq: integer().notNull(),
	tool: text().notNull(),
	server: text(),
	args: jsonb(),
	resultDigest: jsonb("result_digest"),
	status: text().notNull(),
	latencyMs: integer("latency_ms"),
	error: jsonb(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_tool_calls_turn_run_id_seq").on(table.turnRunId, table.seq),
	foreignKey({
			columns: [table.turnRunId],
			foreignColumns: [turnRun.id],
			name: "tool_calls_turn_run_id_fkey"
		}).onDelete("cascade"),
	unique("uq_tool_calls_turn_run_id_seq").on(table.turnRunId, table.seq),
	adminRead("tool_calls"),
	owner("tool_calls"),
	check("ck_tool_calls_status", sql`status = ANY (ARRAY['ok'::text, 'error'::text])`),
]).enableRLS();

export const memoryOutbox = pgTable("memory_outbox", {
	conversationId: uuid("conversation_id").primaryKey().notNull(),
	userSub: text("user_sub").notNull(),
	lastTurnAt: timestamp("last_turn_at", { withTimezone: true, mode: 'string' }).notNull(),
	status: text().default('pending').notNull(),
	attempts: integer().default(0).notNull(),
	lastError: jsonb("last_error"),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.conversationId],
			foreignColumns: [conversations.id],
			name: "memory_outbox_conversation_id_fkey"
		}).onDelete("cascade"),
	pgPolicy("memory_outbox_worker", { as: "permissive", for: "all", to: poseidonWorker, using: sql`true`, withCheck: sql`true` }),
	owner("memory_outbox"),
	check("ck_memory_outbox_status", sql`status = ANY (ARRAY['pending'::text, 'done'::text, 'failed'::text])`),
]).enableRLS();

export const userProfile = pgTable("user_profile", {
	userSub: text("user_sub").primaryKey().notNull(),
	systemInstruction: text("system_instruction").default('').notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	owner("user_profile"),
]).enableRLS();

export const messages = pgTable("messages", {
	id: uuid().primaryKey().notNull(),
	conversationId: uuid("conversation_id").notNull(),
	userSub: text("user_sub").notNull(),
	role: text(),
	parts: jsonb().notNull(),
	turnId: uuid("turn_id"),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_messages_conversation_order").on(table.conversationId, table.createdAt, table.id),
	foreignKey({
			columns: [table.conversationId],
			foreignColumns: [conversations.id],
			name: "messages_conversation_id_fkey"
		}).onDelete("cascade"),
	owner("messages"),
	check("ck_messages_role", sql`role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text])`),
]).enableRLS();

export const conversations = pgTable("conversations", {
	id: uuid().primaryKey().notNull(),
	userSub: text("user_sub").notNull(),
	title: text().default('New chat').notNull(),
	mode: text().default('default'),
	state: jsonb().default({}).notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	archived: boolean().default(false).notNull(),
}, (table) => [
	index("ix_conversations_user_recency").on(table.userSub, table.updatedAt.desc().nullsFirst(), table.id.desc().nullsFirst()),
	owner("conversations"),
	check("ck_conversations_mode", sql`mode = ANY (ARRAY['existing'::text, 'prospect'::text, 'default'::text])`),
]).enableRLS();

export const turnRun = pgTable("turn_run", {
	id: uuid().primaryKey().notNull(),
	kind: text().default('chat_turn').notNull(),
	conversationId: uuid("conversation_id"),
	messageId: uuid("message_id"),
	userSub: text("user_sub").notNull(),
	clientTurnKey: uuid("client_turn_key"),
	turnIndex: integer("turn_index"),
	question: text(),
	mode: text(),
	parsed: jsonb().default({}).notNull(),
	answerSummary: text("answer_summary"),
	status: text().notNull(),
	inputTokens: integer("input_tokens").default(0).notNull(),
	outputTokens: integer("output_tokens").default(0).notNull(),
	latencyMs: integer("latency_ms"),
	traceId: text("trace_id"),
	error: jsonb(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	finishedAt: timestamp("finished_at", { withTimezone: true, mode: 'string' }),
	redactedAt: timestamp("redacted_at", { withTimezone: true, mode: 'string' }),
}, (table) => [
	index("ix_turn_run_conversation_id_turn_index").on(table.conversationId, table.turnIndex),
	index("ix_turn_run_created_at").on(table.createdAt),
	unique("uq_turn_run_user_sub_client_turn_key").on(table.userSub, table.clientTurnKey),
	adminRead("turn_run"),
	owner("turn_run"),
	check("ck_turn_run_kind", sql`kind = ANY (ARRAY['chat_turn'::text, 'memory_update'::text])`),
	check("ck_turn_run_status", sql`status = ANY (ARRAY['running'::text, 'ok'::text, 'clarify'::text, 'error'::text])`),
]).enableRLS();

export const llmCalls = pgTable("llm_calls", {
	id: uuid().primaryKey().notNull(),
	turnRunId: uuid("turn_run_id").notNull(),
	userSub: text("user_sub").notNull(),
	seq: integer().notNull(),
	provider: text().notNull(),
	modelId: text("model_id").notNull(),
	role: text().notNull(),
	promptVersion: text("prompt_version").notNull(),
	promptHash: text("prompt_hash").notNull(),
	inputTokens: integer("input_tokens").default(0).notNull(),
	outputTokens: integer("output_tokens").default(0).notNull(),
	latencyMs: integer("latency_ms"),
	status: text().notNull(),
	error: jsonb(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_llm_calls_turn_run_id_seq").on(table.turnRunId, table.seq),
	foreignKey({
			columns: [table.turnRunId],
			foreignColumns: [turnRun.id],
			name: "llm_calls_turn_run_id_fkey"
		}).onDelete("cascade"),
	unique("uq_llm_calls_turn_run_id_seq").on(table.turnRunId, table.seq),
	adminRead("llm_calls"),
	owner("llm_calls"),
	check("ck_llm_calls_status", sql`status = ANY (ARRAY['ok'::text, 'error'::text])`),
]).enableRLS();

export const messageFeedback = pgTable("message_feedback", {
	id: uuid().primaryKey().notNull(),
	messageId: uuid("message_id").notNull(),
	runId: uuid("run_id").notNull(),
	userSub: text("user_sub").notNull(),
	verdict: text(),
	comment: text(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.messageId],
			foreignColumns: [messages.id],
			name: "message_feedback_message_id_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.runId],
			foreignColumns: [turnRun.id],
			name: "message_feedback_run_id_fkey"
		}),
	unique("uq_message_feedback_message_id_user_sub").on(table.messageId, table.userSub),
	adminRead("message_feedback"),
	owner("message_feedback"),
	check("ck_message_feedback_verdict", sql`verdict = ANY (ARRAY['up'::text, 'down'::text])`),
]).enableRLS();

export const userMemory = pgTable("user_memory", {
	userSub: text("user_sub").notNull(),
	version: integer().notNull(),
	entries: jsonb().notNull(),
	createdBy: text("created_by").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	primaryKey({ columns: [table.userSub, table.version], name: "user_memory_pkey"}),
	owner("user_memory"),
	check("ck_user_memory_created_by", sql`created_by = ANY (ARRAY['user'::text, 'distiller'::text])`),
]).enableRLS();
