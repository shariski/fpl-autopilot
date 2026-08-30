// Mirrors docs/api-contract.md exactly. (forthcoming) fields are nullable.
export type Mode = 'auto' | 'manual' | 'hybrid' | 'deadguard' | 'frozen';
export type Position = 'GKP' | 'DEF' | 'MID' | 'FWD';
export type PlayerStatus = 'a' | 'd' | 'i' | 's' | 'u';
export type Chip = 'wildcard' | 'free_hit' | 'bench_boost' | 'triple_captain';

export interface Banner {
	level: 'info' | 'warning' | 'error';
	text: string;
	action?: { label: string; endpoint: string };
}

export interface Status {
	current_gw: number;
	next_gw: number | null;
	deadline_utc: string;
	mode: Mode;
	data_fresh_as_of_utc: string;
	frozen: boolean;
	banners: Banner[];
}

export interface SquadPlayer {
	id: number;
	web_name: string;
	position: Position;
	team_short: string;
	price: number;
	status: PlayerStatus;
	is_captain: boolean;
	is_vice_captain: boolean;
	multiplier: number; // 0 = bench, 1 = starter, 2 = captain
	xp_next: number | null; // (forthcoming)
	xp_next5: number | null; // (forthcoming)
}

export interface Squad {
	gw: number;
	bank: number;
	team_value: number;
	free_transfers: number | null; // (forthcoming, auth-only)
	players: SquadPlayer[]; // exactly 15
}

export interface CaptainPick {
	player_id: number;
	web_name: string;
	xp: number;
	fixture: string;
	reason: string;
	reasoning?: string;
	reasoning_source?: 'ai' | 'classic';
}
export interface Captain {
	picks: CaptainPick[]; // top 5, ranked; [] until built
	vice_player_id: number | null;
}

export interface TransferSide {
	player_id: number;
	web_name: string;
	price: number;
}
export interface TransferSuggestion {
	out: TransferSide;
	in: TransferSide;
	ep_delta_5gw: number;
	hit_cost: number; // 0, -4, -8 ...
	confidence: number;
	reasoning?: string;
	reasoning_source?: 'ai' | 'classic';
}
export interface Transfers {
	suggestions: TransferSuggestion[]; // [] if none worth it
	empty_reason: string | null;
}

export interface ChipRecommendation {
	chip: Chip;
	reason: string;
	reasoning?: string;
	reasoning_source?: 'ai' | 'classic';
}
export interface Chips {
	recommendation: ChipRecommendation | null;
}

export interface PlannerCell {
	gw: number;
	opponent_short: string;
	home: boolean;
	fdr_attack: number; // 1-5
	fdr_defense: number; // 1-5
}
export interface PlannerRow {
	player_id: number;
	web_name: string;
	position: Position;
	team_short: string;
	cells: (PlannerCell | null)[]; // null = blank GW
}
export interface Planner {
	horizon: number[];
	rows: PlannerRow[];
}

export interface ActivityEntry {
	ts_utc: string;
	gw: number;
	mode: Mode;
	decision_type: 'captain' | 'transfer' | 'bench' | 'chip' | 'deadguard';
	action_taken: string;
	executed: boolean;
}
export interface Activity {
	entries: ActivityEntry[];
}

export interface ApiError {
	error: string;
}

// Aggregate the client returns in one call (one fetch fan-out later).
export interface Dashboard {
	status: Status;
	squad: Squad;
	captain: Captain;
	transfers: Transfers;
	chips: Chips;
	planner: Planner;
	activity: Activity;
}

export type MockScenario = 'full' | 'launch';

// ---------- Audit (S-G) ----------
export interface AuditResidual {
	activity_log_id: number;
	gw: number;
	decision_type: string;
	subject_player_ids: number[];
	expected_points: number;
	actual_points: number;
	residual: number;
	model_version: string;
	inputs_summary: Record<string, unknown>;
}

export interface AuditAggregateStat {
	n: number;
	mean_residual: number;
	stddev: number;
	ci_95: [number, number];
}

export interface AuditProposal {
	parameter: string;
	current_value: number;
	proposed_value: number;
	justification: string;
	n_observations: number;
	confidence: 'high' | 'medium' | 'low';
	bounded_range: [number, number] | null;
}

export interface AuditReport {
	gw_range: [number, number];
	generated_at: string;
	model_version: string;
	residuals: AuditResidual[];
	cluster_counts: Record<string, number>;
	aggregate_trends: Record<string, AuditAggregateStat>;
	proposals: AuditProposal[];
	narrative: string | null;
	narrative_provider: string | null;
}

export interface Insight {
	category: 'overperformance' | 'fixture_alignment' | 'minutes_role' | 'value_market';
	claim: string;
	evidence_used: string[];
	confidence: 'high' | 'medium' | 'low';
	implication: string;
}

export interface PlayerIdentity {
	name: string | null;
	web_name: string;
	position: string;
	team: string;
	price: number | null;
}

export interface PlayerInsight {
	status: 'cached' | 'generated' | 'unavailable';
	player_id: number;
	player: PlayerIdentity | null;
	gw: number | null;
	insights: Insight[];
	summary: string;
	data_limits: string[];
	model_id: string | null;
	generated_at: string | null;
}

export interface SquadPick {
	player_id: number;
	web_name: string;
	team: string;
	position: Position;
	price: number;
	xp_6gw: number;
	slot: string;
	reason: string;
	spike_bonus?: number | null;
}

export interface SpikeSignal {
	player_id: number;
	level: 'high' | 'medium';
	reason: string;
	web_name?: string;
	team?: string | null;
}

export interface Speculation {
	spikes: SpikeSignal[];
	drops: SpikeSignal[];
	differentials: SpikeSignal[];
	market_read: string;
}

export interface SquadBuilder {
	status: 'cached' | 'generated' | 'unavailable';
	gw: number | null;
	source: 'ai' | 'deterministic';
	picks: SquadPick[];
	template_rationale: string;
	risks: string[];
	budget_used: number;
	speculation: Speculation | null;
	model_id: string | null;
	generated_at: string | null;
	data_basis?: { as_of_utc: string | null; xp_model_version: string };
}

export interface SpeculationNote {
	id: number;
	note: string;
	team_id: number | null;
	player_id: number | null;
	team_short: string | null;
	player_name: string | null;
	created_at: string;
	active: boolean;
}

export interface LeaderCohortRow {
	entry_id: number;
	player_name: string;
	entry_name: string;
	rank: number;
	total: number;
	last_gw_points: number | null;
	transfers: number | null;
	hit_cost: number | null;
	bank: number | null;
	value: number | null;
	chips_used: string[];
	past_rank: number | null;
}

export interface LeadersPayload {
	cohort: LeaderCohortRow[];
	patterns: {
		chip_timing: { rows: { chip: string; gw: number; count: number }[]; first_chip: Record<string, { gw: number; count: number }> };
		transfers: { mean_per_gw: number | null; median_per_gw: number | null; hit_freq: number | null;
					 mean_hit_cost: number | null; histogram: { transfers: number; count: number }[] };
		bank_value: { bank: { gw: number; mean: number; median: number }[]; value: { gw: number; mean: number; median: number }[] };
		momentum: { top_movers: { entry_id: number; player_name?: string; from_gw: number; to_gw: number; rank_gain: number }[];
					sustained_elite: number[] };
		ownership: { gw: number; cohort: number; rows: { player_id: number; web_name: string; team_short: string; count: number; pct: number; differential: boolean }[] };
		captaincy: { gw: number; rows: { player_id: number; web_name: string; team_short: string; count: number }[] };
		formations: { gw: number; rows: { formation: string; count: number }[] };
		progression: { series: { entry_id: number; player_name: string; points: { gw: number; rank: number }[] }[] };
		retention: { gw1_cohort: number; by_gw: { gw: number; retained: number; pct: number }[] };
	};
}
