-- Synthetic data only. Built into app.db by the integration test (no binary in git).
CREATE TABLE accounts (id INTEGER PRIMARY KEY, tc5_code TEXT, notes TEXT);
INSERT INTO accounts VALUES (1, 'zz-synthetic-sentinel-m4-db1', 'synthetic');
INSERT INTO accounts VALUES (2, 'zz-synthetic-sentinel-m4-db2', NULL);
CREATE TABLE events (event_id INTEGER, tc2_ref TEXT);
INSERT INTO events VALUES (1, 'zz-synthetic-sentinel-m4-ev1');
