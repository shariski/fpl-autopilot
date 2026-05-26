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
