<script lang="ts">
	let { name, size = 20 }: { name: string; size?: number } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
	{#if name === 'overview'}
		<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" />
	{:else if name === 'energy'}
		<path d="M13 2 4 14h7l-1 8 10-13h-7z" />
	{:else if name === 'navigation'}
		<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15m6-12v15" />
	{:else if name === 'more'}
		<circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" />
	{:else if name === 'activity'}
		<path d="M4 19V5m0 14h16M7 15l4-4 3 2 5-6" />
	{:else if name === 'protection'}
		<path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" />
	{:else if name === 'settings'}
		<circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.5.9l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.5-.9l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-1.8l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.5-.9l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.5.9l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1-.1 1.8Z" transform="translate(-1 -1)" />
	{:else if name === 'arrow'}
		<path d="M5 12h14m-6-6 6 6-6 6" />
	{:else if name === 'plug'}
		<path d="M8 3v5m8-5v5M7 8h10v3a5 5 0 0 1-5 5v5m-4-8h8" />
	{:else if name === 'sun'}
		<circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
	{:else if name === 'moon'}
		<path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z" />
	{:else if name === 'system'}
		<rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8m-4-4v4" />
	{:else if name === 'download'}
		<path d="M12 3v12m-5-5 5 5 5-5M4 19v2h16v-2" />
	{:else if name === 'upload'}
		<path d="M12 15V3m-5 5 5-5 5 5M4 19v2h16v-2" />
	{:else if name === 'plus'}
		<path d="M12 5v14m-7-7h14" />
	{:else if name === 'check'}
		<path d="m5 12 4 4L19 6" />
	{:else if name === 'close'}
		<path d="m6 6 12 12M18 6 6 18" />
	{:else if name === 'lock'}
		<rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 1 1 8 0v3" />
	{:else if name === 'unlock'}
		<rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0" />
	{:else if name === 'climate'}
		<path d="M12 2v20m0-13 4-4m-4 4L8 5m4 7 5 3m-5-3-5 3m5 3 4 3m-4-3-4 3" />
	{:else if name === 'charge'}
		<path d="M13 2 5 13h6l-1 9 9-13h-6z" />
	{/if}
</svg>
