import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ActivityLog from './ActivityLog.svelte';
import { fullMock } from '$lib/mocks/full';

describe('ActivityLog', () => {
	it('renders entries with action text', () => {
		render(ActivityLog, { props: { activity: fullMock.activity } });
		expect(screen.getByText('Captain set to Haaland')).toBeInTheDocument();
	});
	it('shows an empty message when there are no entries', () => {
		render(ActivityLog, { props: { activity: { entries: [] } } });
		expect(screen.getByText(/No decisions logged yet/i)).toBeInTheDocument();
	});
});
