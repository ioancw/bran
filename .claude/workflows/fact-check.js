// fact-check — verify a document's factual claims against the web and
// produce a corrected copy.
//
// Run by name: Workflow({ name: 'fact-check', args: { path, output? } })
// where `path` is a file the agents can Read (a briefing, a report) and
// `output`, if given, is where the checked document goes — pass the same
// path to correct the document in place (the morning-brief runner does; the
// verification note then records what changed). Without `output` the
// original is left alone and the copy goes to <name>.checked.md. The
// script holds the plan; agents do the reading, searching, judging and
// rewriting; the hand-offs between stages are data, not prose, so nothing
// gets retyped.
//
// Stages:  extract claims → verify each (in parallel) → skeptic tries to
//          refute every supported claim → rewrite the affected passages into
//          <name>.checked.md (the original is left alone) → changelog.
//
// What a verdict does to the document:
//   not supported, high confidence  → the sentence is corrected to what the evidence says
//   not supported, lower confidence → the sentence is hedged and marked [unverified]
//   disputed (skeptic found contrary evidence) → the sentence keeps its claim and gains a caveat
//   unchecked (verifier failed)      → marked [unverified], text unchanged
//   supported                        → untouched

export const meta = {
  name: 'fact-check',
  description: 'Verify a document’s factual claims on the web and write a corrected copy next to it',
  whenToUse: 'The user asks to check, verify, audit or fact-check a document or briefing',
  phases: [
    { title: 'Extract', detail: 'one reader pulls out the checkable claims' },
    { title: 'Verify', detail: 'one verifier per claim, searching independently' },
    { title: 'Refute', detail: 'a skeptic tries to knock down each supported claim' },
    { title: 'Correct', detail: 'one editor rewrites the affected passages into a .checked.md copy' },
    { title: 'Report', detail: 'one writer compiles the changelog' },
  ],
}

const MAX_CLAIMS = 12
const path = typeof args === 'string' ? args : args && args.path
if (!path) throw new Error('fact-check needs args.path: the document to check')
const explicitOutput = typeof args === 'object' && args && typeof args.output === 'string' && args.output.trim()
const checkedPath = explicitOutput || path.replace(/\.(md|markdown|txt)$/i, '') + '.checked.md'
const inPlace = checkedPath === path

const CLAIMS = {
  type: 'object',
  properties: {
    document_title: { type: 'string' },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string', description: 'One checkable factual statement, self-contained' },
          quote: { type: 'string', description: 'The exact sentence in the document that makes the claim, verbatim' },
          source_hint: { type: 'string', description: 'The source the document gives for it, if any (domain or name)' },
          importance: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
        required: ['claim', 'quote', 'importance'],
      },
    },
  },
  required: ['claims'],
}

const VERDICT = {
  type: 'object',
  properties: {
    supported: { type: 'boolean' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    evidence: { type: 'string', description: 'What you found, one or two sentences, with the specific figure or date' },
    url: { type: 'string', description: 'The single best source URL (empty string if none)' },
    correction: { type: 'string', description: 'If not supported: the original sentence minimally rewritten so it is accurate — same point, same length, same voice; change only the wrong part (empty if supported)' },
  },
  required: ['supported', 'confidence', 'evidence', 'url', 'correction'],
}

const REFUTATION = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    url: { type: 'string', description: 'Source for the refutation (empty if none)' },
  },
  required: ['refuted', 'reason', 'url'],
}

const EDITS = {
  type: 'object',
  properties: {
    output_path: { type: 'string' },
    changes: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          action: { type: 'string', enum: ['corrected', 'hedged', 'caveated', 'flagged'] },
          before: { type: 'string', description: 'The original sentence, verbatim' },
          after: { type: 'string', description: 'The sentence as it now reads' },
        },
        required: ['action', 'before', 'after'],
      },
    },
  },
  required: ['output_path', 'changes'],
}

// --- Extract ---------------------------------------------------------------
phase('Extract')
const extracted = await agent(
  `Read the document at ${path} with the Read tool. List its checkable factual claims: specific figures, dates, ` +
    `named decisions, quantities, rankings — things a search could confirm or contradict. Skip opinions, forecasts ` +
    `phrased as possibilities, and the document's own structure. Return at most ${MAX_CLAIMS}, most important first. ` +
    `For each give (a) the claim self-contained — subject, number and date, so it can be checked without the ` +
    `document — and (b) the exact sentence from the document that makes it, copied verbatim so an editor can find it.`,
  { schema: CLAIMS, label: 'extract claims' },
)
const claims = ((extracted && extracted.claims) || []).slice(0, MAX_CLAIMS)
log(`${claims.length} claims extracted from ${extracted && extracted.document_title ? extracted.document_title : path}`)
if (!claims.length) return { path, checked_path: inPlace ? path : null, claims: [], changes: [], report: 'No checkable claims were found in the document.' }

// --- Verify, then Refute — each claim flows through both stages on its own ---
const checked = await pipeline(
  claims,
  (c, _item, i) =>
    agent(
      `Verify this claim using web search (and page reads where needed): "${c.claim}". ` +
        (c.source_hint ? `The document attributes it to: ${c.source_hint}. ` : '') +
        `Find primary or authoritative sources. Decide whether the claim is supported as stated — the exact figure, ` +
        `date and direction matter; a forecast reported as a fact, or the wrong period, is NOT supported. ` +
        `If it is not supported, write the correction as the ORIGINAL sentence with only its wrong part fixed — keep its ` +
        `point, length and voice; do not add unrelated facts. Return the best single URL.`,
      { schema: VERDICT, phase: 'Verify', label: `verify ${i + 1}`, agentType: 'research' },
    ).then((v) => ({ claim: c, verdict: v })),
  (r, _item, i) => {
    if (!r || !r.verdict) return r
    if (!r.verdict.supported) return { ...r, refutation: null }
    return agent(
      `A verifier says this claim is supported: "${r.claim.claim}" — evidence: ${r.verdict.evidence} (${r.verdict.url || 'no URL'}). ` +
        `Try to REFUTE it: search for more recent, more authoritative or contradicting information (a later revision, ` +
        `a different official figure, a misread date). Default to refuted=false unless you find concrete contrary evidence.`,
      { schema: REFUTATION, phase: 'Refute', label: `refute ${i + 1}`, agentType: 'research' },
    ).then((ref) => ({ ...r, refutation: ref }))
  },
)

const results = checked.filter(Boolean)
const dropped = claims.length - results.length
if (dropped) log(`${dropped} claim(s) could not be checked (verifier failed)`)

// Plain code decides the outcome and the edit each row earns — no model here.
const rows = results.map((r) => {
  const v = r.verdict
  const ref = r.refutation
  const status = !v ? 'unchecked' : !v.supported ? 'not supported' : ref && ref.refuted ? 'disputed' : 'supported'
  const action =
    status === 'not supported' ? (v.confidence === 'high' ? 'corrected' : 'hedged')
    : status === 'disputed' ? 'caveated'
    : status === 'unchecked' ? 'flagged'
    : 'none'
  return { claim: r.claim.claim, quote: r.claim.quote, importance: r.claim.importance, status, action, verdict: v, refutation: ref }
})
const counts = rows.reduce((acc, r) => ((acc[r.status] = (acc[r.status] || 0) + 1), acc), {})
const edits = rows.filter((r) => r.action !== 'none')
log(`verdicts: ${JSON.stringify(counts)} — ${edits.length} passage(s) to edit`)

// --- Correct ---------------------------------------------------------------
let corrected = null
if (edits.length || explicitOutput) {
  phase('Correct')
  corrected = await agent(
    `You are editing a document so that it no longer states things the evidence does not support. ` +
      `Read ${path} with the Read tool, apply the edits below (there may be none), and save the full edited document with ` +
      `the Write tool to exactly this path: ${checkedPath}` +
      (inPlace ? ` (the same file — overwrite it). ` : `. Leave the original file untouched. `) +
      `Change nothing except the listed passages — but if the same wrong or disputed fact is repeated elsewhere in the ` +
      `document (a summary bullet, a "So what" line), fix it there too, the same way.\n\n` +
      `Edits (JSON). For each: find the sentence in "quote" (it is verbatim from the document; if the wording differs ` +
      `slightly, find the sentence that makes the same claim).\n` +
      `- action "corrected": replace the sentence with the verdict's "correction" — a minimal fix of the original, not a ` +
      `new paragraph; keep the document's voice, tense and length, keep its existing citation, and add the verdict's url ` +
      `only if the correction rests on it.\n` +
      `- action "hedged": keep the subject but reword the sentence so it no longer asserts the unsupported part ` +
      `(expected / reported / likely), then append " [unverified]".\n` +
      `- action "caveated": keep the sentence and append ONE short clause (under 25 words) with the refutation's "reason" ` +
      `(e.g. ", though <source> reports <contrary point>").\n` +
      `- action "flagged": keep the sentence and append " [unverified]".\n\n` +
      `${JSON.stringify(edits.map((e) => ({ action: e.action, quote: e.quote, claim: e.claim, correction: e.verdict && e.verdict.correction, url: e.verdict && e.verdict.url, reason: e.refutation && e.refutation.reason, refutation_url: e.refutation && e.refutation.url })), null, 2)}\n\n` +
      `At the very end of the document add a section "## Verification note" with one line per edit (the original ` +
      `wording in quotes, what it now says and why, with the source), and one line saying how many claims were checked ` +
      `and confirmed (${JSON.stringify(counts)}). ` +
      `Return the output path and, for every edit you made, the sentence before and after.`,
    { schema: EDITS, label: edits.length ? 'apply corrections' : 'add verification note', effort: edits.length ? undefined : 'low' },
  )
  if (!corrected) log('the editor failed; no corrected copy was written')
} else {
  log('every claim held up; no corrected copy needed')
}
if (corrected && edits.length) log(`${corrected.changes.length} passage(s) edited → ${corrected.output_path}`)

// --- Report ----------------------------------------------------------------
phase('Report')
const report = await agent(
  `Write a short changelog in markdown for a fact-check of the document at ${path}. Use the JSON below verbatim — ` +
    `do not re-verify or soften anything.\n\n` +
    `Verdicts: ${JSON.stringify(rows, null, 2)}\n\n` +
    `Edits applied${corrected ? (inPlace ? ` (the document was corrected in place at ${corrected.output_path})` : ` (corrected copy saved to ${corrected.output_path})`) : ' (none; no copy written)'}: ` +
    `${JSON.stringify(corrected ? corrected.changes : [], null, 2)}\n\n` +
    `Structure: one paragraph with the counts (${JSON.stringify(counts)}) and, if a checked document was written, its path ` +
    `(say "in place" when it is the original's path). ` +
    `Then "## Changed": one bullet per edit — the sentence before, the sentence after, and the source as a markdown link; ` +
    `mark high-importance ones with ⚠. Then "## Confirmed": one short line per supported claim with its source link ` +
    `(no evidence text). Then "## Could not check" if any. Return only the markdown.`,
  { label: 'compile changelog', effort: 'low' },
)

return { path, checked_path: corrected ? corrected.output_path : null, counts, claims: rows, changes: corrected ? corrected.changes : [], report }
