-- Run once against the META database. Does not modify DW tables.
CREATE TABLE IF NOT EXISTS business_glossary (
  id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  term VARCHAR(128) NOT NULL,
  definition TEXT NOT NULL,
  aliases JSON NOT NULL,
  guidance TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_by VARBINARY(128) NOT NULL,
  updated_by VARBINARY(128) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_business_glossary_term (term),
  INDEX idx_business_glossary_enabled (enabled, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
