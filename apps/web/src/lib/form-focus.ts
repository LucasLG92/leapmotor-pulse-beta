/** Restore a closed editor's focus only when it has actually been lost. */
export function restoreFormFocus(opener: HTMLElement | null, fallback: HTMLElement | null, closedEditor?: HTMLFormElement): void {
	const active = document.activeElement;
	// Some engines retain the hidden editor's active element until the next task.
	const hiddenEditorFocus = active instanceof HTMLElement && !!closedEditor?.contains(active)
		&& !active.getClientRects().length;
	if (!document.hasFocus() || (active !== document.body && !hiddenEditorFocus)) return;
	const visible = (element: HTMLElement | null): element is HTMLElement =>
		!!element?.isConnected && !!element.getClientRects().length
		&& !element.closest('[hidden]') && !element.matches(':disabled');
	const target = visible(opener) ? opener : visible(fallback) ? fallback : null;
	if (target) {
		target.focus({ preventScroll: true });
		target.scrollIntoView({ block: 'center', behavior: 'instant' });
	}
}
