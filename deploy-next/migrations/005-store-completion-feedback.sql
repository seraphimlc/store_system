-- Telemetry-only additive capability; caller owns the transaction, after 004.
-- Preserve old event kinds and rows. Never downgrade schema when rolling code back.
ALTER TABLE bd_store_search_feedback DROP CONSTRAINT IF EXISTS bd_store_search_feedback_kind_check;
ALTER TABLE bd_store_search_feedback ADD CONSTRAINT bd_store_search_feedback_kind_check
 CHECK(kind IN ('opened','confirmed','suggestion_selected','completion_selected'));
ALTER TABLE bd_store_search_feedback DROP CONSTRAINT IF EXISTS bd_store_search_feedback_check;
ALTER TABLE bd_store_search_feedback ADD CONSTRAINT bd_store_search_feedback_check
 CHECK((kind IN ('opened','confirmed') AND store_task_id IS NOT NULL AND suggested_name='') OR (kind IN ('suggestion_selected','completion_selected') AND store_task_id IS NULL AND suggested_name<>''));
