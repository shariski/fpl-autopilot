import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import SectionNav from './SectionNav.svelte';

describe('SectionNav', () => {
	it('shows the chip nav item only when a chip is recommended', () => {
		const { unmount } = render(SectionNav, { props: { hasChip: false } });
		expect(screen.queryByRole('link', { name: /chip/i })).toBeNull();
		unmount();
		render(SectionNav, { props: { hasChip: true } });
		expect(screen.getByRole('link', { name: /chip/i })).toBeInTheDocument();
	});
	it('always shows Team, Captain, Transfers, Fixtures, Log', () => {
		render(SectionNav, { props: { hasChip: false } });
		for (const label of [/team/i, /captain/i, /transfers/i, /fixtures/i, /log/i])
			expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
	});
});
