import type { PlannerCell, Position } from './types';

/** Clamp an FDR to 1-5 and return its CSS colour token. Presentational only. */
export function fdrToken(value: number): string {
	const v = Math.min(5, Math.max(1, Math.round(value)));
	return `var(--fdr-${v})`;
}

/** Per api-contract.md: attackers coloured by fdr_attack, defenders by fdr_defense. */
export function cellFdr(position: Position, cell: PlannerCell): number {
	return position === 'FWD' || position === 'MID' ? cell.fdr_attack : cell.fdr_defense;
}
