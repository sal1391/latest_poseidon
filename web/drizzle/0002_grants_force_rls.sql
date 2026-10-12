-- Phase 16 Task 7: what Drizzle cannot express, transcribed from
-- backend/migrations/versions/0004-0010. Order: FORCE RLS, grants, memberships.

-- FORCE ROW LEVEL SECURITY (0004 conversations/messages; 0005 turn_run/llm_calls/tool_calls;
-- 0006 message_feedback; 0008 user_profile/user_memory/memory_outbox): the table OWNER is
-- filtered too, which is what makes test_owner_connection_is_still_filtered_by_force_rls pass.
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE turn_run FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE llm_calls FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE tool_calls FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE message_feedback FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE user_profile FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE user_memory FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE memory_outbox FORCE ROW LEVEL SECURITY;
--> statement-breakpoint

-- Schema usage (0004 app, 0005 admin, 0009 worker).
GRANT USAGE ON SCHEMA public TO poseidon_app;
--> statement-breakpoint
GRANT USAGE ON SCHEMA public TO poseidon_admin;
--> statement-breakpoint
GRANT USAGE ON SCHEMA public TO poseidon_worker;
--> statement-breakpoint

-- poseidon_app DML. Deliberate gaps: no DELETE on the audit tables (0005: rows are redacted,
-- never removed) nor on message_feedback (0006); no UPDATE on user_memory (0008: versions are
-- immutable).
GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON turn_run, llm_calls, tool_calls TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON message_feedback TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON user_profile TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, DELETE ON user_memory TO poseidon_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON memory_outbox TO poseidon_app;
--> statement-breakpoint

-- poseidon_admin read (0005, 0006). Nothing on the personalization tables (0008, by design).
GRANT SELECT ON turn_run, llm_calls, tool_calls TO poseidon_admin;
--> statement-breakpoint
GRANT SELECT ON message_feedback TO poseidon_admin;
--> statement-breakpoint

-- poseidon_worker (0009).
GRANT SELECT, UPDATE ON memory_outbox TO poseidon_worker;
--> statement-breakpoint

-- Memberships (0009, 0010): a non-superuser DSN can only SET ROLE to a role it is a member of.
-- CURRENT_USER is whoever runs `drizzle-kit migrate`, exactly as it was whoever ran alembic.
GRANT poseidon_worker TO CURRENT_USER;
--> statement-breakpoint
GRANT poseidon_app TO CURRENT_USER;
