-- A separate database for the integration tests.
--
-- The test fixtures TRUNCATE between tests, so pointing TEST_DATABASE_URL at the development
-- database wipes your seeded kana and your own learner row halfway through a session. Keeping a
-- dedicated database means `make test` and `make dev` can run side by side.
SELECT 'CREATE DATABASE nihongo_test OWNER nihongo'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'nihongo_test')\gexec
