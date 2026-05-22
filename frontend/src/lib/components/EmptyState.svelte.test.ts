import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import EmptyState from './EmptyState.svelte';

describe('EmptyState', () => {
	it('renders the provided message', () => {
		render(EmptyState, { props: { message: 'No transfers worth making this GW.' } });
		expect(screen.getByText('No transfers worth making this GW.')).toBeInTheDocument();
	});
});
