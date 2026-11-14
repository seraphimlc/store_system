/** Search-only stage gate, one existing disposable DB; no production or Docker deployment. */
import './store-search.test';
import './store-search-related.test';
import './store-search-name-index.test';
import './store-name-retrieval.test';
import './store-search-scope.test';
import './store-search-feedback.test';
import './store-completion-feedback.test';
import './store-search-suggestions.test';
import './store-search-completions.test';
import './store-search-http.test';
import './store-search-resilience.test';
import './store-search-connections.test';
// Retention seeds telemetry without business fixtures; keep it last in this isolated gate.
import './store-search-retention.test';
