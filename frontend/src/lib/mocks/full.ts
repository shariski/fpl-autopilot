import type { Dashboard, SquadPlayer, PlannerRow } from '../types';

const players: SquadPlayer[] = [
	// Starting XI (multiplier >= 1)
	{ id: 1, web_name: 'Raya', position: 'GKP', team_short: 'ARS', price: 5.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 3.9, xp_next5: 18.2 },
	{ id: 2, web_name: 'Gabriel', position: 'DEF', team_short: 'ARS', price: 6.3, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.4, xp_next5: 20.1 },
	{ id: 3, web_name: 'Saliba', position: 'DEF', team_short: 'ARS', price: 6.1, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.2, xp_next5: 19.6 },
	{ id: 4, web_name: 'Gvardiol', position: 'DEF', team_short: 'MCI', price: 6.5, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.0, xp_next5: 18.4 },
	{ id: 5, web_name: 'Hall', position: 'DEF', team_short: 'NEW', price: 5.4, status: 'd', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 3.6, xp_next5: 16.8 },
	{ id: 6, web_name: 'Salah', position: 'MID', team_short: 'LIV', price: 13.1, status: 'a', is_captain: false, is_vice_captain: true, multiplier: 1, xp_next: 6.1, xp_next5: 28.0 },
	{ id: 7, web_name: 'Saka', position: 'MID', team_short: 'ARS', price: 10.4, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 5.4, xp_next5: 24.7 },
	{ id: 8, web_name: 'Palmer', position: 'MID', team_short: 'CHE', price: 10.8, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 5.0, xp_next5: 23.1 },
	{ id: 9, web_name: 'Mbeumo', position: 'MID', team_short: 'BRE', price: 7.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.6, xp_next5: 21.0 },
	{ id: 10, web_name: 'Haaland', position: 'FWD', team_short: 'MCI', price: 14.7, status: 'a', is_captain: true, is_vice_captain: false, multiplier: 2, xp_next: 7.2, xp_next5: 31.4 },
	{ id: 11, web_name: 'Isak', position: 'FWD', team_short: 'NEW', price: 9.3, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.8, xp_next5: 22.3 },
	// Bench (multiplier 0)
	{ id: 12, web_name: 'Sels', position: 'GKP', team_short: 'NFO', price: 5.0, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 3.2, xp_next5: 14.9 },
	{ id: 13, web_name: 'Lacroix', position: 'DEF', team_short: 'CRY', price: 4.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 3.0, xp_next5: 13.7 },
	{ id: 14, web_name: 'Rogers', position: 'MID', team_short: 'AVL', price: 5.7, status: 'i', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 0.0, xp_next5: 8.1 },
	{ id: 15, web_name: 'Watkins', position: 'FWD', team_short: 'AVL', price: 9.0, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 4.1, xp_next5: 19.2 }
];

const horizon = [38, 39, 40, 41, 42];

// FDR cells per player; opponent/home illustrative; one team has a blank GW (null).
const opp: Record<number, ([string, boolean, number, number] | null)[]> = {
	1: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	2: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	3: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	4: [['WHU', true, 1, 2], ['CHE', false, 4, 4], ['TOT', true, 3, 3], ['FUL', false, 2, 2], ['BOU', true, 2, 3]],
	5: [['MCI', false, 5, 5], ['ARS', true, 4, 5], null, ['BUR', false, 1, 1], ['WOL', true, 2, 2]],
	6: [['WHU', false, 2, 2], ['CRY', true, 2, 2], ['BOU', false, 3, 3], ['BHA', true, 3, 3], ['MCI', false, 5, 5]],
	7: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	8: [['EVE', true, 2, 2], ['WOL', false, 2, 2], ['ARS', true, 5, 5], ['NEW', false, 4, 4], ['FUL', true, 2, 2]],
	9: [['FUL', true, 2, 2], ['TOT', false, 4, 3], ['EVE', true, 2, 2], ['LIV', false, 5, 5], ['CRY', true, 2, 2]],
	10: [['BOU', true, 3, 3], ['AVL', false, 3, 3], ['CHE', true, 4, 4], ['SOU', false, 1, 1], ['FUL', true, 2, 2]],
	11: [['ARS', true, 4, 5], ['MCI', false, 5, 5], ['WHU', true, 2, 2], ['BRE', false, 3, 3], ['EVE', true, 2, 2]],
	12: [['CHE', true, 4, 4], ['BHA', false, 3, 3], ['MUN', true, 3, 4], ['WHU', false, 2, 2], ['LEE', true, 1, 1]],
	13: [['LEE', true, 1, 1], ['BUR', false, 1, 1], ['NEW', true, 4, 4], ['ARS', false, 5, 5], ['SUN', true, 1, 1]],
	14: [['SUN', true, 1, 1], ['BRE', false, 2, 2], ['MCI', true, 5, 5], ['BOU', false, 3, 3], ['NFO', true, 2, 2]],
	15: [['SUN', true, 1, 1], ['BRE', false, 2, 2], ['MCI', true, 5, 5], ['BOU', false, 3, 3], ['NFO', true, 2, 2]]
};

const rows: PlannerRow[] = players.map((p) => ({
	player_id: p.id,
	web_name: p.web_name,
	position: p.position,
	team_short: p.team_short,
	cells: opp[p.id].map((c, i) =>
		c === null
			? null
			: { gw: horizon[i], opponent_short: c[0], home: c[1], fdr_attack: c[2], fdr_defense: c[3] }
	)
}));

export const fullMock: Dashboard = {
	status: {
		current_gw: 38,
		next_gw: null,
		deadline_utc: '2026-05-24T13:00:00Z',
		mode: 'manual',
		data_fresh_as_of_utc: '2026-05-22T09:00:00Z',
		frozen: false,
		banners: [{ level: 'warning', text: 'Understat data is 8 days stale.' }]
	},
	squad: { gw: 37, bank: 2.3, team_value: 99.7, free_transfers: 1, players },
	captain: {
		picks: [
			{ player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BOU (H)', reason: 'Highest xP (7.2). Next best Salah 6.1 — gap 1.1. Home vs FDR-3 defense.' },
			{ player_id: 6, web_name: 'Salah', xp: 6.1, fixture: 'WHU v LIV (A)', reason: 'Second highest xP. Strong away record; FDR-2 attack matchup.' },
			{ player_id: 7, web_name: 'Saka', xp: 5.4, fixture: 'ARS v BOU (H)', reason: 'Home vs FDR-2 attack. On set pieces.' },
			{ player_id: 8, web_name: 'Palmer', xp: 5.0, fixture: 'CHE v EVE (H)', reason: 'Penalties + home vs FDR-2.' },
			{ player_id: 11, web_name: 'Isak', xp: 4.8, fixture: 'NEW v ARS (H)', reason: 'In form, but FDR-4 defense caps ceiling.' }
		],
		vice_player_id: 6
	},
	transfers: {
		suggestions: [
			{ out: { player_id: 5, web_name: 'Hall', price: 5.4 }, in: { player_id: 101, web_name: 'Aina', price: 5.0 }, ep_delta_5gw: 2.6, hit_cost: 0, confidence: 74 },
			{ out: { player_id: 14, web_name: 'Rogers', price: 5.7 }, in: { player_id: 102, web_name: 'Gordon', price: 7.5 }, ep_delta_5gw: 4.1, hit_cost: -4, confidence: 69 },
			{ out: { player_id: 13, web_name: 'Lacroix', price: 4.6 }, in: { player_id: 103, web_name: 'Andersen', price: 4.6 }, ep_delta_5gw: 1.4, hit_cost: 0, confidence: 61 }
		],
		empty_reason: null
	},
	chips: {
		recommendation: { chip: 'bench_boost', reason: 'DGW: all 15 have fixtures; combined bench xP 5.2 (> threshold 4).' }
	},
	planner: { horizon, rows },
	activity: {
		entries: [
			{ ts_utc: '2026-05-22T19:30:00Z', gw: 38, mode: 'manual', decision_type: 'captain', action_taken: 'Captain set to Haaland', executed: false },
			{ ts_utc: '2026-05-22T03:05:00Z', gw: 38, mode: 'manual', decision_type: 'transfer', action_taken: 'Generated 3 transfer suggestions', executed: false },
			{ ts_utc: '2026-05-22T03:01:00Z', gw: 38, mode: 'manual', decision_type: 'bench', action_taken: 'Recomputed FDR for GW38–42', executed: false }
		]
	}
};
