export function internalAppPath(url: URL, origin: string, paths: readonly string[]): string | null {
	if (url.origin !== origin || url.username || url.password || url.search || !paths.includes(url.pathname)) return null;
	if (url.hash && !(url.pathname === '/settings' && url.hash === '#preparation')) return null;
	return url.pathname + url.hash;
}

export function preparationSection(path: string): 'preparation' | null {
	return path === '/settings#preparation' ? 'preparation' : null;
}
