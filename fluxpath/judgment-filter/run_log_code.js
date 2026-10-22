// Runs the filter's local-log snippets the way Open WebUI's Chat.svelte runs an "execute" event:
//   new Function(`return (async () => { ${code} })()`)
// against a minimal localStorage, then checks what was stored.
const fs = require('fs');

const store = new Map();
globalThis.localStorage = {
	getItem: (k) => (store.has(k) ? store.get(k) : null),
	setItem: (k, v) => store.set(k, String(v))
};

(async () => {
	const codes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
	let last;
	for (const code of codes) {
		last = await new Function(`return (async () => { ${code} })()`)();
	}
	const list = JSON.parse(localStorage.getItem('fluxpath.judgments'));
	const newest = list[list.length - 1];
	const ok =
		last === list.length &&
		newest.judge.mode === 'M2' &&
		newest.judge.mock === true &&
		newest.outcome === 'sent' &&
		typeof newest.ts === 'string';
	console.log(`${ok ? 'ok' : 'bad'} ${list.length} log [${newest.judge.mode}]`);
	process.exit(ok ? 0 : 1);
})();
