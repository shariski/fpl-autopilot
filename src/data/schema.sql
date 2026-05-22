CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY,
  name TEXT,
  web_name TEXT,
  team_id INTEGER,
  position TEXT,
  price REAL,
  status TEXT,
  ownership REAL,
  form REAL,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY,
  name TEXT,
  short_name TEXT,
  strength_attack_home INTEGER,
  strength_attack_away INTEGER,
  strength_defence_home INTEGER,
  strength_defence_away INTEGER
);

CREATE TABLE IF NOT EXISTS player_stats (
  player_id INTEGER,
  gw INTEGER,
  source TEXT,
  minutes INTEGER,
  goals INTEGER,
  assists INTEGER,
  xg REAL,
  xa REAL,
  bonus INTEGER,
  total_points INTEGER,
  PRIMARY KEY (player_id, gw, source)
);

CREATE TABLE IF NOT EXISTS fixtures (
  id INTEGER PRIMARY KEY,
  gw INTEGER,
  home_team_id INTEGER,
  away_team_id INTEGER,
  kickoff_utc TIMESTAMP,
  finished BOOLEAN,
  home_score INTEGER,
  away_score INTEGER
);

CREATE TABLE IF NOT EXISTS fdr (
  team_id INTEGER,
  gw INTEGER,
  fdr_attack INTEGER,
  fdr_defense INTEGER,
  computed_at TIMESTAMP,
  PRIMARY KEY (team_id, gw)
);

CREATE TABLE IF NOT EXISTS xp (
  player_id INTEGER,
  gw INTEGER,
  model_version TEXT,
  xp REAL,
  xminutes REAL,
  xgoals REAL,
  xassists REAL,
  xcs REAL,
  computed_at TIMESTAMP,
  PRIMARY KEY (player_id, gw, model_version)
);

CREATE TABLE IF NOT EXISTS my_team (
  gw INTEGER PRIMARY KEY,
  picks_json TEXT,
  bank REAL,
  team_value REAL,
  free_transfers INTEGER,
  chips_used_json TEXT,
  snapshot_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gameweeks (
  id INTEGER PRIMARY KEY,
  name TEXT,
  deadline_utc TIMESTAMP,
  is_current BOOLEAN,
  is_next BOOLEAN,
  finished BOOLEAN,
  state TEXT NOT NULL DEFAULT 'PENDING',
  last_user_action_at TIMESTAMP,
  last_system_action_at TIMESTAMP,
  deadguard_triggered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TIMESTAMP,
  gw INTEGER,
  mode TEXT,
  decision_type TEXT,
  action_taken TEXT,
  inputs_json TEXT,
  alternatives_json TEXT,
  executed BOOLEAN,
  exec_outcome_json TEXT
);

CREATE TABLE IF NOT EXISTS credentials (
  id INTEGER PRIMARY KEY,
  fpl_email_encrypted BLOB,
  fpl_password_encrypted BLOB,
  session_cookie_encrypted BLOB,
  csrf_token_encrypted BLOB,
  session_last_refreshed TIMESTAMP,
  auth_state TEXT DEFAULT 'active',
  relogin_failures INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cache_meta (
  resource TEXT PRIMARY KEY,
  last_fetched_utc TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS understat_players (
  understat_id TEXT PRIMARY KEY,
  fpl_player_id INTEGER,
  season TEXT,
  player_name TEXT,
  team_title TEXT,
  games INTEGER,
  minutes INTEGER,
  goals INTEGER,
  assists INTEGER,
  xg REAL,
  xa REAL,
  npg INTEGER,
  npxg REAL,
  xg_per_90 REAL,
  xa_per_90 REAL,
  updated_at TIMESTAMP
);
