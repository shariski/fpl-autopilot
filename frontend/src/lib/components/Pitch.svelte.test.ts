import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Pitch from './Pitch.svelte';
import { fullMock } from '$lib/mocks/full';
import { launchMock } from '$lib/mocks/launch';

describe('Pitch', () => {
	it('renders all 15 players', () => {
		render(Pitch, { props: { squad: fullMock.squad } });
		for (const p of fullMock.squad.players)
			expect(screen.getAllByText(p.web_name).length).toBeGreaterThan(0);
	});
	it('marks the captain with a C armband', () => {
		render(Pitch, { props: { squad: fullMock.squad } });
		expect(screen.getByLabelText('captain')).toHaveTextContent('C');
	});
	it('shows xP when present and an em-dash when forthcoming (launch)', () => {
		const { unmount } = render(Pitch, { props: { squad: fullMock.squad } });
		expect(screen.getAllByText('7.2').length).toBeGreaterThan(0);
		unmount();
		render(Pitch, { props: { squad: launchMock.squad } });
		expect(screen.getAllByText('—').length).toBeGreaterThan(0);
	});
});
