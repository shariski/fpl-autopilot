import type { Dashboard } from '../types';
import { fullMock } from './full';

// Real day-one: live data present (status, squad core, FDR planner); forthcoming = null/empty.
export const launchMock: Dashboard = {
	status: fullMock.status,
	squad: {
		...fullMock.squad,
		free_transfers: null,
		players: fullMock.squad.players.map((p) => ({ ...p, xp_next: null, xp_next5: null }))
	},
	captain: { picks: [], vice_player_id: null },
	transfers: { suggestions: [], empty_reason: null },
	chips: { recommendation: null },
	planner: fullMock.planner,
	activity: { entries: [] }
};
