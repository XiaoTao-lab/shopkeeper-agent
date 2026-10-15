-- Run once against the META database. Does not modify DW tables.
CREATE TABLE IF NOT EXISTS chat_session (
  id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  owner_subject VARBINARY(128) NOT NULL,
  role_snapshot VARCHAR(64) NOT NULL,
  scope_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  title VARCHAR(100) NOT NULL DEFAULT '新会话',
  context_json JSON NOT NULL,
  active_run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  deleted_at DATETIME(6) NULL,
  INDEX idx_chat_owner (owner_subject, deleted_at, updated_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS query_run (
  id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  client_request_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  raw_query TEXT NOT NULL,
  rewritten_query TEXT NULL,
  status VARCHAR(16) NOT NULL,
  outcome VARCHAR(16) NULL,
  terminal_event JSON NULL,
  role_snapshot VARCHAR(64) NOT NULL,
  scope_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  trace_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL,
  deadline_at DATETIME(6) NOT NULL,
  finished_at DATETIME(6) NULL,
  end_reason VARCHAR(32) NULL,
  CONSTRAINT fk_query_run_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  UNIQUE KEY uq_run_request (session_id, client_request_id),
  INDEX idx_run_session (session_id, created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
