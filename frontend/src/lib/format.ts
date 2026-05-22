/** Render a numeric value, or an em-dash when it is null/undefined (forthcoming fields). */
export function dash(value: number | null | undefined, decimals?: number): string {
	if (value === null || value === undefined) return '—';
	return decimals === undefined ? String(value) : value.toFixed(decimals);
}

/** Format money in £m, e.g. 14.7 -> "£14.7". */
export function money(value: number): string {
	return `£${value.toFixed(1)}`;
}

/** Human countdown from now (ms) to an ISO deadline. */
export function countdown(deadlineUtc: string, now: number = Date.now()): string {
	const diff = new Date(deadlineUtc).getTime() - now;
	if (diff <= 0) return 'Deadline passed';
	const totalMin = Math.floor(diff / 60000);
	const days = Math.floor(totalMin / 1440);
	const hours = Math.floor((totalMin % 1440) / 60);
	const mins = totalMin % 60;
	return days > 0 ? `${days}d ${hours}h ${mins}m` : `${hours}h ${mins}m`;
}
